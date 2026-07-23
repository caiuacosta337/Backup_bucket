# Backup Bucket

Incremental file backup service that uploads new or changed files to Amazon S3 (or S3-compatible endpoints).

The app supports:

- One-shot backup runs.
- Continuous polling mode.
- Event-driven mode using watchdog.
- Retention cleanup for old date-based backups.
- Optional zip upload mode.
- Optional SMTP notifications.

## How It Works

1. Reads files from source_path.
2. Calculates SHA-256 for each file.
3. Compares hashes with state_file.
4. Uploads only new or changed files to S3.
5. Removes old backup folders based on retention_days.
6. Writes updated hashes to state_file.

## Repository Structure

- backup.py: Main application.
- config.example.yml: Generic sample config.
- config.windows-docker.yml: Windows + Docker sample config.
- run-backup.ps1: Windows helper script for docker run.
- requirements.txt: Python dependencies.
- Dockerfile: Container image build.
- k8s/: Kubernetes manifests.

## Prerequisites

Choose one runtime path: local Python, Docker, or Kubernetes.

Local Python:

- Python 3.12+.
- Access to the source folder you want to back up.
- AWS credentials with S3 access.

Docker:

- Docker Desktop or Docker Engine.
- Access to source folder and a writable local state folder.

Kubernetes:

- Cluster access and kubectl.
- PVCs for source data and state.
- Secret with AWS credentials.

## Step-by-Step: Local Python Run

1. Create and activate a virtual environment.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

2. Install dependencies.

```powershell
pip install -r requirements.txt
```

3. Create config.yml from the sample.

```powershell
Copy-Item config.example.yml config.yml
```

4. Edit config.yml and set at least:

- source_path
- bucket
- region
- retention_days

5. Set AWS credentials in the same shell session.

```powershell
$env:AWS_ACCESS_KEY_ID="your-access-key"
$env:AWS_SECRET_ACCESS_KEY="your-secret-key"
$env:AWS_DEFAULT_REGION="sa-east-1"
```

6. Run one backup cycle.

```powershell
python backup.py --config config.yml
```

7. Optional continuous modes:

Polling every 60 seconds:

```powershell
python backup.py --config config.yml --watch-mode poll --watch-interval 60
```

Event mode with 1 second wake checks:

```powershell
python backup.py --config config.yml --watch-mode events --watch-interval 1
```

## Step-by-Step: Docker Run (Recommended on Windows)

1. Build image.

```powershell
docker build -t backup-service .
```

2. Prepare config.yml (or use config.windows-docker.yml) and set bucket/region.

3. Set AWS credentials in current PowerShell session.

```powershell
$env:AWS_ACCESS_KEY_ID="your-access-key"
$env:AWS_SECRET_ACCESS_KEY="your-secret-key"
$env:AWS_DEFAULT_REGION="sa-east-1"
```

4. Run one-shot backup.

```powershell
docker run --rm `
  -v C:/Temp/LOGS:/data/source `
  -v ${PWD}/state:/app/state `
  -v ${PWD}/config.windows-docker.yml:/app/config.yml `
  -e AWS_ACCESS_KEY_ID=$env:AWS_ACCESS_KEY_ID `
  -e AWS_SECRET_ACCESS_KEY=$env:AWS_SECRET_ACCESS_KEY `
  -e AWS_DEFAULT_REGION=$env:AWS_DEFAULT_REGION `
  backup-service python backup.py --config /app/config.yml
```

5. Run continuous polling mode.

```powershell
docker run --rm `
  -v C:/Temp/LOGS:/data/source `
  -v ${PWD}/state:/app/state `
  -v ${PWD}/config.windows-docker.yml:/app/config.yml `
  -e AWS_ACCESS_KEY_ID=$env:AWS_ACCESS_KEY_ID `
  -e AWS_SECRET_ACCESS_KEY=$env:AWS_SECRET_ACCESS_KEY `
  -e AWS_DEFAULT_REGION=$env:AWS_DEFAULT_REGION `
  backup-service python backup.py --config /app/config.yml --watch-mode poll --watch-interval 60
```

6. Optional: use the helper script.

```powershell
.\run-backup.ps1
```

Notes:

- On Windows Docker bind mounts, polling mode is usually more reliable than events mode.
- Keep state mounted at /app/state so hash history survives restarts.

## Step-by-Step: Kubernetes Deployment

1. Create storage resources.

```bash
kubectl apply -f k8s/state-pvc.yaml
kubectl apply -f k8s/source-pvc.example.yaml
```

2. Review and update config values in k8s/configmap.yaml.

- bucket
- region
- retention_days
- s3_prefix

3. Create AWS secret.

Option A: edit k8s/secret.example.yaml and apply.

```bash
kubectl apply -f k8s/secret.example.yaml
```

Option B: create secret directly.

```bash
kubectl create secret generic backup-aws \
  --from-literal=AWS_ACCESS_KEY_ID=your-access-key \
  --from-literal=AWS_SECRET_ACCESS_KEY=your-secret-key \
  --from-literal=AWS_DEFAULT_REGION=sa-east-1
```

4. Apply ConfigMap.

```bash
kubectl apply -f k8s/configmap.yaml
```

5. Deploy one mode:

Daily job at 02:00:

```bash
kubectl apply -f k8s/cronjob.yaml
```

Continuous watcher deployment:

```bash
kubectl apply -f k8s/deployment-watch.yaml
```

6. Verify resources.

```bash
kubectl get cronjob,pods
kubectl logs deployment/backup-bucket-watch
```

Important:

- Ensure the image tag in manifests exists in GHCR.
- If image is private, configure imagePullSecret.

## Configuration Reference

Minimum required fields:

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
```

Behavior notes:

- watch_interval_seconds: 0 means one-shot mode.
- compress: true uploads one zip per cycle.
- endpoint_url: set only for S3-compatible providers (for example MinIO).

## Verify It Is Working

After a run:

1. Check logs in console and in state/backup.log.
2. Confirm state/backup_state.json was created or updated.
3. Confirm S3 objects exist under:

YYYY-MM-DD/<relative-path>

or, if s3_prefix is set:

<s3_prefix>/YYYY-MM-DD/<relative-path>

## Troubleshooting

If no files are uploaded:

1. Check source_path exists and is readable.
2. Confirm AWS credentials are set in the same shell session.
3. Confirm bucket name and region are correct.
4. Run one-shot mode first before enabling watch mode.

If docker run fails with permission or missing path errors:

1. Verify host folders exist (source and state).
2. Use forward-slash mount style on Windows, for example C:/Temp/LOGS:/data/source.
3. Confirm config file is mounted to /app/config.yml.

If Kubernetes pods fail to start:

1. Check image pull status with kubectl describe pod.
2. Validate backup-aws secret exists.
3. Validate PVCs are bound.
4. Check logs with kubectl logs <pod-name>.

If credentials are valid but uploads still fail:

1. Validate IAM permissions include s3:ListBucket, s3:GetObject, s3:PutObject, s3:DeleteObject for target prefix.
2. Check bucket policy for explicit deny rules.

## Exit Codes

- 0: Success.
- 1: Partial failure (some files failed).
- 2: Fatal error (config or S3 connectivity).

## Next Recommended Setup for This Repository

For your current Windows path C:/Temp/LOGS, start with:

1. config.windows-docker.yml.
2. watch_mode: poll.
3. watch_interval_seconds: 60.
4. run-backup.ps1.

This gives stable behavior first, then you can experiment with watch_mode: events later.
