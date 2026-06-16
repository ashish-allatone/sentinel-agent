import os
import io
import zipfile
from enum import Enum
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse

app = FastAPI(title="Script Server API")

class OSType(str, Enum):
    windows = "windows"
    linux = "linux"

SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "scripts")


def build_zip(files: dict[str, str]) -> io.BytesIO:
    """
    files: { arcname_in_zip: absolute_path_on_disk }
    Returns an in-memory zip buffer.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
        for arcname, path in files.items():
            if not os.path.exists(path):
                raise HTTPException(
                    status_code=404,
                    detail=f"Required file '{arcname}' was not found on the server.",
                )
            zipf.write(path, arcname=arcname)
    buffer.seek(0)
    return buffer


def zip_response(buffer: io.BytesIO, filename: str) -> StreamingResponse:
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/Agent_Scripts")
async def download_agent_script(
    os_type: OSType = Query(..., description="Target operating system")
):
    if os_type is OSType.windows:
        files = {
            "sentinel-agent.exe": os.path.join(SCRIPTS_DIR, "sentinel-agent.exe"),
            "setup.ps1": os.path.join(SCRIPTS_DIR, "setup.ps1"),
        }
    else:  # linux
        files = {
            "sentinel-agent": os.path.join(SCRIPTS_DIR, "sentinel-agent"),
            "setup.sh": os.path.join(SCRIPTS_DIR, "setup.sh"),
        }

    buffer = build_zip(files)
    return zip_response(buffer, "agent.zip")


@app.get("/Architecture_Scripts")
async def download_arch_script(
    os_type: OSType = Query(..., description="Target operating system")
):
    if os_type is OSType.windows:
        files = {
            "docker-compose.yml": os.path.join(SCRIPTS_DIR, "docker-compose.yml"),
            "I_setup.ps1": os.path.join(SCRIPTS_DIR, "I_setup.ps1"),
        }
    else:  # linux
        files = {
            "docker-compose.yml": os.path.join(SCRIPTS_DIR, "docker-compose.yml"),
            "I_setup.sh": os.path.join(SCRIPTS_DIR, "I_setup.sh"),
        }

    buffer = build_zip(files)
    return zip_response(buffer, "Arch.zip")