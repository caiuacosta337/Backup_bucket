<!-- This README explains the backup workflow, Docker usage, and Windows-specific run path. -->
# Backup Bucket

Production-inspired automated backup service for sending new or modified files to Amazon S3.

This project is designed for the scenario you described:

- Source files live on a host directory such as `C:\Temp\LOGS` on Windows or `/opt/application/data` on Linux.
- The backup process runs in a container or directly on the host.
- Only new or changed files are uploaded.
- Old backups are removed automatically using a retention policy.

## Recommended Architecture

For your case, a Docker container with a bind mount is a good fit, but the container should usually run as a scheduled job, not as a passive mount-only process.

Why:

- A bind mount exposes the host folder to the container.
- The backup process still needs to actively scan or react to file system events.
- For Docker Desktop with a Windows host bind mount, periodic polling is still the more reliable default.
- Native file system event watching is now supported too, and works best on Linux hosts or environments where inotify-style events propagate cleanly.

This repository supports both modes:

- One-shot backup execution for cron, Task Scheduler, or CI.
- Polling mode for repeated scans every N seconds.
- Event mode for real file-change triggered backups with debounce.

## Features

- Detects new and modified files using SHA-256 hashes.
- Uploads to S3 under a date prefix such as `2026-07-22/...`.
- Skips unchanged files.
- Writes audit logs to `backup.log`.
- Continues on per-file failures and returns meaningful exit codes.
- Deletes backup prefixes older than the configured retention window.
- Optional ZIP compression for changed files.
- Optional SMTP email notification.
- Supports AWS S3 and S3-compatible endpoints such as MinIO.

## Project Files

- `backup.py`: main backup service
- `config.example.yml`: sample configuration
- `requirements.txt`: Python dependencies
- `Dockerfile`: container image definition
- `run-backup.ps1`: Windows launcher
- `k8s/`: Kubernetes manifests for CronJob and watch deployment
- `.github/workflows/publish-image.yml`: multi-arch image publishing to GHCR

## Portfolio Pitch

Use this summary when presenting the project to recruiters:

- Designed an incremental backup service that protects app files with S3-based retention.
- Implemented hash-based change detection (SHA-256) to avoid redundant uploads.
- Added fault tolerance with per-file error handling and audit logging.
- Containerized the service and provided Windows + Kubernetes run paths.
- Automated portable image publishing to GitHub Container Registry.

## Configuration

Copy `config.example.yml` to `config.yml` and adjust values.

```yaml
source_path: /data/source
bucket: backup-bucket-local-files
region: sa-east-1
retention_days: 30
compress: false
s3_prefix: ""
log_file: /app/state/backup.log
state_file: /app/state/backup_state.json
endpoint_url: null
watch_mode: poll
watch_interval_seconds: 0
watch_debounce_seconds: 10
email:
  enabled: false
  host: smtp.example.com
  port: 587
  username: alerts@example.com
  password: change-me
  from: alerts@example.com
  to:
    - ops@example.com
  use_tls: true
```

There is also a concrete Windows Docker config in `config.windows-docker.yml` and a PowerShell launcher in `run-backup.ps1`.

## AWS Credentials

Provide credentials with standard AWS environment variables:

```powershell
$env:AWS_ACCESS_KEY_ID="your-access-key"
$env:AWS_SECRET_ACCESS_KEY="your-secret-key"
$env:AWS_DEFAULT_REGION="sa-east-1"
```

You can also use an IAM role when running on AWS infrastructure.

## Run Locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item config.example.yml config.yml
python backup.py --config config.yml
```

Optional polling mode:

```powershell
python backup.py --config config.yml --watch-interval 60
```

Optional event mode:

```powershell
python backup.py --config config.yml --watch-mode events --watch-interval 1
```

## Run With Docker

Build the image:

```powershell
docker build -t backup-service .
```

Run against your Windows logs folder:

```powershell
docker run --rm \
	-v C:/Temp/LOGS:/data/source \
	-v ${PWD}/state:/app/state \
	-v ${PWD}/config.yml:/app/config.yml \
	-e AWS_ACCESS_KEY_ID=your-access-key \
	-e AWS_SECRET_ACCESS_KEY=your-secret-key \
	-e AWS_DEFAULT_REGION=sa-east-1 \
	backup-service python backup.py --config /app/config.yml
```

If you want the container to keep polling for changes:

```powershell
docker run --rm \
	-v C:/Temp/LOGS:/data/source \
	-v ${PWD}/state:/app/state \
	-v ${PWD}/config.yml:/app/config.yml \
	-e AWS_ACCESS_KEY_ID=your-access-key \
	-e AWS_SECRET_ACCESS_KEY=your-secret-key \
	-e AWS_DEFAULT_REGION=sa-east-1 \
	backup-service python backup.py --config /app/config.yml --watch-interval 60
```

If you want event-driven watching in the container instead of polling:

```powershell
docker run --rm \
	-v C:/Temp/LOGS:/data/source \
	-v ${PWD}/state:/app/state \
	-v ${PWD}/config.windows-docker.yml:/app/config.yml \
	-e AWS_ACCESS_KEY_ID=your-access-key \
	-e AWS_SECRET_ACCESS_KEY=your-secret-key \
	-e AWS_DEFAULT_REGION=sa-east-1 \
	backup-service python backup.py --config /app/config.yml --watch-mode events --watch-interval 1
```

For your Windows host setup, start with polling mode unless you have confirmed that file system events propagate correctly through your Docker mount.

## Publish On GitHub

Your remote is already configured as:

- `https://github.com/caiuacosta337/Backup_bucket.git`

Run the following from this repository root:

```powershell
git add .
git commit -m "feat: add S3 backup service with Docker and Kubernetes support"
git push origin main
```

If `git commit` fails because your identity is not set, run:

```powershell
git config --global user.name "Your Name"
git config --global user.email "you@example.com"
```

Then repeat commit and push.

## Publish Reusable Container Image

This project includes `.github/workflows/publish-image.yml`, which builds and pushes a multi-arch image for `linux/amd64` and `linux/arm64`.

How to publish:

1. Push this repository to GitHub.
2. Create a tag and push it:

```powershell
git tag v1.0.0
git push origin v1.0.0
```

3. GitHub Actions will push image tags to:

- `ghcr.io/caiuacosta337/backup-bucket:latest` (default branch)
- `ghcr.io/caiuacosta337/backup-bucket:v1.0.0` (tag)

You can also run the workflow manually from the Actions tab via `workflow_dispatch`.

## Kubernetes Usage (Any OS)

Kubernetes runs Linux containers regardless of your local OS, so any user on Windows, macOS, or Linux can deploy this as long as they have a cluster.

Manifests included:

- `k8s/configmap.yaml`: backup config
- `k8s/secret.example.yaml`: AWS credentials template (do not commit real values)
- `k8s/state-pvc.yaml`: state storage claim
- `k8s/source-pvc.example.yaml`: source data claim example
- `k8s/cronjob.yaml`: daily backup at 02:00
- `k8s/deployment-watch.yaml`: continuous polling backup service

Apply flow:

```bash
kubectl apply -f k8s/state-pvc.yaml
kubectl apply -f k8s/source-pvc.example.yaml
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/secret.example.yaml
kubectl apply -f k8s/cronjob.yaml
```

For continuous mode instead of CronJob:

```bash
kubectl apply -f k8s/deployment-watch.yaml
```

Important:

- Replace placeholder values in `k8s/secret.example.yaml` before applying.
- If your cluster cannot pull GHCR private images, make the package public or configure an `imagePullSecret`.

## Windows Quick Start

1. Edit `config.windows-docker.yml` and set your real bucket name.
2. Set AWS credentials in your PowerShell session.
3. Run `./run-backup.ps1`.

The script uses `C:\Temp\LOGS` as the source folder and stores runtime state in a local `state` directory next to the repository.

## S3 Layout

Without compression, changed files are uploaded under the current date prefix:

```text
your-bucket/
	2026-07-22/
		app.log
		audit/events.json
```

With compression enabled, each cycle uploads a ZIP file:

```text
your-bucket/
	2026-07-22/
		backup-20260722-200005.zip
```

## Scheduling

Linux cron example:

```cron
0 2 * * * /usr/bin/python3 /opt/backup/backup.py --config /opt/backup/config.yml
```

Docker-based cron example:

```cron
0 2 * * * docker run --rm -v /opt/application/data:/data/source -v /opt/backup/state:/app/state -v /opt/backup/config.yml:/app/config.yml --env-file /opt/backup/aws.env backup-service python backup.py --config /app/config.yml
```

On Windows, use Task Scheduler to run the equivalent `docker run` command.

## Exit Codes

- `0`: backup completed without failures
- `1`: backup completed with partial failures
- `2`: fatal configuration or connectivity failure

## Notes For Your Setup

Given that you already have `C:\Temp\LOGS`, the simplest path is:

1. Keep the logs on the host.
2. Mount that folder into the container as `/data/source`.
3. Mount a persistent local `state` folder so hashes and logs survive container restarts.
4. Run the container on a schedule, use `watch_mode: poll` for frequent polling, or test `watch_mode: events` if your environment supports reliable mount event propagation.

If you want, the next step after this is to adapt `config.example.yml` specifically to your bucket name and your exact `C:\Temp\LOGS` layout.
