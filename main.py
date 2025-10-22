import asyncio
import os
import uuid
from typing import Dict, Optional, Literal

from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from yt_dlp import YoutubeDL

# Configuração de pastas
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FILES_DIR = os.getenv("FILES_DIR", os.path.join(BASE_DIR, "files"))
os.makedirs(FILES_DIR, exist_ok=True)

app = FastAPI(title="yt-dlp backend")

# Configuração CORS (útil para testes web)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# memória simples p/ jobs (troque por Redis/DB em produção)
JOBS: Dict[str, dict] = {}

# servir arquivos prontos
app.mount("/files", StaticFiles(directory=FILES_DIR), name="files")

class DownloadReq(BaseModel):
    url: str
    fmt: Literal["mp3", "mp4"] = "mp3"

class StatusRes(BaseModel):
    job_id: str
    status: Literal["queued", "downloading", "done", "error"]
    progress: Optional[float] = None
    message: Optional[str] = None
    file_url: Optional[str] = None
    filename: Optional[str] = None

def build_ydl_opts(job_id: str, fmt: str, allow_missing_pot: bool = False):
    outtmpl = os.path.join(FILES_DIR, f"{job_id}.%(ext)s")

    # Seletores tolerantes
    if fmt == "mp3":
        format_sel = (
            "bestaudio[ext=m4a]/"
            "bestaudio[acodec^=mp4a]/"
            "bestaudio/best"
        )
        post = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "0",
        }]
        merge_out = None
    elif fmt == "mp4":
        format_sel = (
            "bv*[ext=mp4]+ba[ext=m4a]/"
            "bv*+ba/best[ext=mp4]/best"
        )
        post = []
        merge_out = "mp4"
    else:
        format_sel = "best"
        post, merge_out = [], None

    # Sempre use cliente WEB para evitar PO token
    extractor_args = {"youtube": {"player_client": ["web"]}}
    if allow_missing_pot:
        # Aceita formatos marcados como dependentes de PO token
        extractor_args["youtube"]["formats"] = ["missing_pot"]

    ydl_opts = {
        "format": format_sel,
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 10,
        "fragment_retries": 10,
        "http_chunk_size": 10 * 1024 * 1024,
        "concurrent_fragment_downloads": 1,
        "nocheckcertificate": True,
        "prefer_ffmpeg": True,
        "merge_output_format": merge_out,
        "postprocessors": post,
        "extractor_args": extractor_args,
        # Ajuda em casos de "sem formato"
        "ignore_no_formats_error": True,
        
        # Configurações de segurança e desempenho
        "cookiefile": os.path.join(BASE_DIR, "cookies.txt"),
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": "https://www.youtube.com/",
        },
        "noplaylist": True,
        "geo_bypass": True,
    }
    
    # Adiciona FFmpeg local se existir
    ffmpeg_path = os.path.join(BASE_DIR, "ffmpeg", "bin")
    if os.path.exists(ffmpeg_path):
        ydl_opts["ffmpeg_location"] = ffmpeg_path
        
    return ydl_opts

async def run_download(job_id: str, url: str, fmt: str):
    job = JOBS[job_id]
    job["status"] = "downloading"
    job["progress"] = 0.0
    job["message"] = "Iniciando…"

    def _hook(d):
        # d['status']: 'downloading'|'finished'
        if d.get("status") == "downloading":
            p = d.get("_percent_str", "").strip().replace("%", "")
            try:
                job["progress"] = float(p)
            except Exception:
                pass
            job["message"] = d.get("_eta_str", "…")
        elif d.get("status") == "finished":
            job["message"] = "Processando…"

    def _run_once(opts):
        with YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=True)

    try:
        loop = asyncio.get_running_loop()
        ydl_opts = build_ydl_opts(job_id, fmt, allow_missing_pot=False)
        ydl_opts["progress_hooks"] = [_hook]

        # Primeira tentativa
        info = await loop.run_in_executor(None, lambda: _run_once(ydl_opts))

        # Sucesso na primeira tentativa
        ext = "mp3" if fmt == "mp3" else "mp4"
        filename = f"{job_id}.{ext}"
        final_path = os.path.join(FILES_DIR, filename)

        job["status"] = "done"
        job["progress"] = 100.0
        job["filename"] = filename
        job["file_url"] = f"/files/{filename}"
        job["message"] = "Concluído"

    except Exception as e:
        msg = str(e)
        # Retry com allow_missing_pot quando o erro é de formato indisponível
        if "Requested format is not available" in msg or "Only images are available" in msg:
            job["message"] = "Re-tentando com fallback de formatos…"
            try:
                # Segunda tentativa com fallback
                ydl_opts = build_ydl_opts(job_id, fmt, allow_missing_pot=True)
                ydl_opts["progress_hooks"] = [_hook]
                
                info = await loop.run_in_executor(
                    None,
                    lambda: _run_once(ydl_opts)
                )
                
                ext = "mp3" if fmt == "mp3" else "mp4"
                filename = f"{job_id}.{ext}"
                final_path = os.path.join(FILES_DIR, filename)

                job["status"] = "done"
                job["progress"] = 100.0
                job["filename"] = filename
                job["file_url"] = f"/files/{filename}"
                job["message"] = "Concluído (fallback)"
                
            except Exception as ee:
                job["status"] = "error"
                job["message"] = f"Falha (formatos indisponíveis): {ee}"
                print(f"YT-DLP FALLBACK ERROR: {ee}")
        else:
            job["status"] = "error"
            job["message"] = f"Erro ao baixar: {msg}"
            print(f"YT-DLP ERROR: {msg}")
            
        # Log detalhado em caso de erro
        if job["status"] == "error":
            import traceback
            print("YT-DLP TRACEBACK:", traceback.format_exc())

@app.post("/api/download", response_model=StatusRes)
async def create_download(req: DownloadReq, bg: BackgroundTasks):
    job_id = uuid.uuid4().hex
    JOBS[job_id] = {
        "status": "queued",
        "progress": 0.0,
        "message": "Na fila…",
        "file_url": None,
        "filename": None,
        "fmt": req.fmt,
    }
    # dispara tarefa
    bg.add_task(run_download, job_id, req.url, req.fmt)
    return StatusRes(job_id=job_id, status="queued", message="OK")

@app.get("/api/status/{job_id}", response_model=StatusRes)
async def get_status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        return JSONResponse(status_code=404, content={"detail": "Job não encontrado"})
    return StatusRes(
        job_id=job_id,
        status=job["status"],
        progress=job.get("progress"),
        message=job.get("message"),
        file_url=job.get("file_url"),
        filename=job.get("filename"),
    )
