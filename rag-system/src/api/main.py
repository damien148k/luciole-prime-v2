"""
FastAPI Application - RAG System API
"""

import os
import sys
import time
from pathlib import Path
from typing import List, Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from loguru import logger
import yaml
import aiofiles

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.ingestion.pipeline import IngestionPipeline
from src.retrieval.query_engine import HybridQueryEngine
from src.generation.llm import LLMGenerator


# Pydantic Models
class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000, description="Question to ask")
    top_k: int = Field(default=20, ge=1, le=30, description="Number of results")
    rerank: bool = Field(default=True, description="Apply reranking")


class QueryResponse(BaseModel):
    response: str
    sources: List[dict]
    confidence: float
    processing_time_ms: int
    model: str


class IngestResponse(BaseModel):
    status: str
    file: str
    chunks: int
    document_id: Optional[str] = None
    error: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    components: dict


# Global instances
ingestion_pipeline = None
query_engine = None
llm_generator = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - initialize components"""
    global ingestion_pipeline, query_engine, llm_generator
    
    logger.info("Initializing RAG System components...")
    
    config_path = "config/settings.yaml"
    
    try:
        # Initialize components
        ingestion_pipeline = IngestionPipeline(config_path)
        query_engine = HybridQueryEngine(config_path)
        llm_generator = LLMGenerator(config_path)
        
        logger.info("RAG System initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize RAG System: {e}")
        logger.warning("Starting in degraded mode - some features may not work")
    
    yield
    
    logger.info("Shutting down RAG System")


# Create FastAPI app
app = FastAPI(
    title="RAG System API",
    description="Local RAG System with Hybrid Search",
    version="1.1.0",
    lifespan=lifespan
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# API Routes
@app.get("/api/health", response_model=HealthResponse)
async def health_check():
    """Check system health"""
    components = {
        "ingestion": ingestion_pipeline is not None,
        "query_engine": query_engine is not None,
        "llm": llm_generator is not None and llm_generator.health_check() if llm_generator else False
    }
    
    if query_engine:
        search_health = query_engine.health_check()
        components.update(search_health)
    
    all_healthy = all(components.values())
    
    return HealthResponse(
        status="healthy" if all_healthy else "degraded",
        components=components
    )


@app.post("/api/ingest", response_model=IngestResponse)
async def ingest_document(file: UploadFile = File(...)):
    """
    Upload and ingest a document
    
    Supported formats: PDF, DOCX, PPTX, XLSX, MSG, EML, TXT
    """
    if not ingestion_pipeline:
        raise HTTPException(status_code=503, detail="Ingestion pipeline not initialized")
    
    # Save uploaded file
    upload_dir = Path("data/documents")
    upload_dir.mkdir(parents=True, exist_ok=True)
    
    file_path = upload_dir / file.filename
    
    try:
        async with aiofiles.open(file_path, "wb") as f:
            content = await file.read()
            await f.write(content)
        
        logger.info(f"File saved: {file_path}")
        
        # Ingest file
        result = ingestion_pipeline.ingest_file(str(file_path))
        
        return IngestResponse(**result)
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Ingestion error: {e}")
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {str(e)}")


@app.post("/api/query", response_model=QueryResponse)
async def query_documents(request: QueryRequest):
    """
    Query the document base with natural language
    """
    if not query_engine or not llm_generator:
        raise HTTPException(status_code=503, detail="Query components not initialized")
    
    start_time = time.time()
    
    try:
        # Get context from query engine
        context_result = query_engine.query_with_context(request.query)
        
        # Generate response
        llm_result = llm_generator.generate(
            query=request.query,
            context=context_result["context"],
            sources=context_result["sources"]
        )
        
        processing_time = int((time.time() - start_time) * 1000)
        
        return QueryResponse(
            response=llm_result["response"],
            sources=llm_result["sources"],
            confidence=llm_result["confidence"],
            processing_time_ms=processing_time,
            model=llm_result["model"]
        )
        
    except Exception as e:
        logger.error(f"Query error: {e}")
        raise HTTPException(status_code=500, detail=f"Query failed: {str(e)}")


@app.get("/api/stats")
async def get_stats():
    """Get system statistics"""
    if not ingestion_pipeline:
        raise HTTPException(status_code=503, detail="Pipeline not initialized")
    
    return ingestion_pipeline.get_stats()


@app.get("/api/documents")
async def list_documents():
    """List indexed documents"""
    docs_dir = Path("data/documents")
    
    if not docs_dir.exists():
        return {"documents": []}
    
    documents = []
    for file_path in docs_dir.iterdir():
        if file_path.is_file():
            documents.append({
                "name": file_path.name,
                "size_bytes": file_path.stat().st_size,
                "modified": file_path.stat().st_mtime
            })
    
    return {"documents": documents}


# Serve static frontend files
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    # Monter le dossier static pour favicon.png, logo.png, etc.
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    
    # Monter le dossier assets seulement s'il existe (apres build Vue.js)
    assets_dir = static_dir / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")
    
    @app.get("/")
    async def serve_frontend():
        """Serve frontend"""
        index_path = static_dir / "index.html"
        if index_path.exists():
            return FileResponse(index_path)
        return JSONResponse({"message": "Frontend not built. Run 'npm run build' in frontend/"})
else:
    @app.get("/")
    async def root():
        """Root endpoint"""
        return {
            "message": "RAG System API",
            "docs": "/docs",
            "health": "/api/health"
        }


# Run with uvicorn
if __name__ == "__main__":
    import uvicorn
    
    with open("config/settings.yaml", "r") as f:
        config = yaml.safe_load(f)
    
    uvicorn.run(
        "main:app",
        host=config["api"]["host"],
        port=config["api"]["port"],
        reload=config["api"]["reload"]
    )

