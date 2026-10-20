"""Job management: async script execution and status endpoints."""

import os
import signal
import subprocess
from flask import Blueprint, jsonify

bp = Blueprint("jobs", __name__)

# In-memory job store
jobs = {}


def _with_venv_python(cmd):
    """Run project scripts with the virtualenv interpreter.

    ``python3`` on PATH can be an older install. YouTube rejects that
    yt-dlp with HTTP 403, so downloads must use the env we update.
    """
    if not cmd or os.path.basename(cmd[0]) not in ("python", "python3"):
        return cmd
    venv_python = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        ".venv", "bin", "python",
    )
    executable = venv_python if os.path.isfile(venv_python) else cmd[0]
    rest = cmd[1:]
    if rest and rest[0] == "-u":
        return [executable, *rest]
    return [executable, "-u", *rest]


def run_script_async(job_id, cmd, cwd=None):
    """Run a script asynchronously and store output."""
    from routes.jobs import jobs  # ensure we reference the module-level dict

    cmd = _with_venv_python(cmd)
    jobs[job_id] = {
        "status": "running",
        "output": "",
        "error": None,
        "return_code": None,
        "pid": None,
    }

    try:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=cwd or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            env=env,
        )

        jobs[job_id]["pid"] = process.pid

        output_lines = []
        for line in process.stdout:
            if jobs[job_id].get("status") == "cancelled":
                break
            output_lines.append(line)
            jobs[job_id]["output"] = "".join(output_lines)

        process.wait()

        if jobs[job_id].get("status") != "cancelled":
            jobs[job_id]["return_code"] = process.returncode
            jobs[job_id]["status"] = "completed" if process.returncode == 0 else "failed"

    except Exception as e:
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(e)


# ---------- endpoints ----------

@bp.route("/api/job/<job_id>")
def get_job_status(job_id):
    """Get status of a running job."""
    if job_id not in jobs:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(jobs[job_id])


@bp.route("/api/job/<job_id>/stop", methods=["POST"])
def stop_job(job_id):
    """Stop a running job."""
    if job_id not in jobs:
        return jsonify({"error": "Job not found"}), 404

    job = jobs[job_id]

    if job["status"] != "running":
        return jsonify({"error": "Job is not running"}), 400

    pid = job.get("pid")
    if not pid:
        return jsonify({"error": "No process ID found"}), 400

    try:
        os.kill(pid, signal.SIGTERM)
        job["status"] = "cancelled"
        job["output"] += "\n\n--- Process stopped by user ---\n"
        return jsonify({"message": "Job stopped", "job_id": job_id})
    except ProcessLookupError:
        job["status"] = "cancelled"
        return jsonify({"message": "Process already ended", "job_id": job_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
