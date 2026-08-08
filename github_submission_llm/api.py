"""Optional HTTP interface for the FNOL claims agent."""

from fastapi import FastAPI, File, HTTPException, Query, UploadFile

from claims_agent import process_upload, process_upload_with_llm

app = FastAPI(title="FNOL Claims Agent", version="1.0.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/claims/process")
async def process_claim(file: UploadFile = File(...), use_llm: bool = Query(False)) -> dict:
    try:
        content = await file.read()
        filename = file.filename or "upload"
        return process_upload_with_llm(filename, content) if use_llm else process_upload(filename, content)
    except (UnicodeDecodeError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
