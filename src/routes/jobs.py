"""Job management: async script execution and status endpoints."""

import os
import re
import signal
import subprocess
import time
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
    job = jobs.setdefault(job_id, {})
    if job.get("status") == "cancelled":
        return
    job.update({
        "status": "running",
        "output": job.get("output", ""),
        "error": job.get("error"),
        "return_code": None,
        "pid": None,
        "kind": job.get("kind", "Task"),
        "label": job.get("label", ""),
        "started_at": job.get("started_at", time.time()),
    })

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
        if jobs[job_id].get("status") == "cancelled":
            process.terminate()
            process.wait()
            return

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
        job["status"] = "cancelled"
        job["output"] = (job.get("output") or "") + "\n\n--- Process stopped by user ---\n"
        return jsonify({"message": "Job stopped", "job_id": job_id})

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


def _match_progress(output):
    """Pull the latest play count out of matcher logs."""
    found = re.findall(r"Progress: (\d+)/(\d+) plays matched", output or "")
    if found:
        done, total = found[-1]
        return {"done": int(done), "total": int(total)}
    planned = re.search(r"Will match first (\d+) plays", output or "")
    if planned:
        return {"done": 0, "total": int(planned.group(1))}
    return None


def _latest_line(output):
    lines = [line.strip() for line in (output or "").splitlines() if line.strip()]
    if not lines:
        return ""
    return lines[-1][:160]


def _log_tail(output, limit=80):
    """Recent lines only. The full log can grow for the whole match."""
    lines = (output or "").splitlines()
    return "\n".join(lines[-limit:])


def _public_job(job_id, job):
    return {
        "id": job_id,
        "status": job.get("status", "running"),
        "kind": job.get("kind", "Task"),
        "label": job.get("label", ""),
        "started_at": job.get("started_at"),
        "error": job.get("error"),
        "progress": _match_progress(job.get("output", "")),
        "latest": _latest_line(job.get("output", "")),
        "log": _log_tail(job.get("output", "")),
    }


@bp.route("/api/jobs")
def list_jobs():
    """List in-memory jobs without their full logs."""
    items = [_public_job(job_id, job) for job_id, job in jobs.items()]
    items.sort(key=lambda item: (item["status"] != "running", -(item["started_at"] or 0)))
    return jsonify({"jobs": items})
