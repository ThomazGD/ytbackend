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

def build_ydl_opts(job_id: str, fmt: str):
    outtmpl = os.path.join(FILES_DIR, f"{job_id}.%(ext)s")

    # Caminho do FFmpeg local
    ffmpeg_path = os.path.join(BASE_DIR, "ffmpeg", "bin")

    # Pós-processamento conforme formato
    postprocessors = []
    merge_output_format = None
    format_sel = "bestaudio/best"

    if fmt == "mp3":
        format_sel = "bestaudio/best"
        postprocessors = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "0",
        }]
        merge_output_format = None
    else:  # mp4
        format_sel = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
        postprocessors = []
        merge_output_format = "mp4"

    ydl_opts = {
        "format": format_sel,
        "outtmpl": outtmpl,
        "quiet": True,
        "noprogress": True,
        "nocheckcertificate": True,
        "merge_output_format": merge_output_format,
        "postprocessors": postprocessors,

        # Configuração do FFmpeg local
        "ffmpeg_location": ffmpeg_path,

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
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "web"],
            }
        },
        "noplaylist": True,
        "geo_bypass": True,
        "progress_hooks": [],
    }
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

    ydl_opts = build_ydl_opts(job_id, fmt)
    ydl_opts["progress_hooks"] = [_hook]

    try:
        loop = asyncio.get_running_loop()
        def _run():
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                return info

        info = await loop.run_in_executor(None, _run)
        
        # Descobrir o arquivo final (mp3/mp4) do job
        import glob
        ext = "mp3" if fmt == "mp3" else "mp4"
        pattern = os.path.join(FILES_DIR, f"{job_id}*.{ext}")
        candidates = glob.glob(pattern)
        
        if not candidates:
            raise RuntimeError("Arquivo final não encontrado (verifique ffmpeg e pós-processamento).")
            
        final_path = max(candidates, key=os.path.getmtime)
        filename = os.path.basename(final_path)

        job["status"] = "done"
        job["progress"] = 100.0
        job["filename"] = filename
        job["file_url"] = f"/files/{filename}"
        job["message"] = "Concluído"
    except Exception as e:
        import traceback
        job["status"] = "error"
        job["message"] = f"{e.__class__.__name__}: {e}"
        print("YT-DLP ERROR:", traceback.format_exc())

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
