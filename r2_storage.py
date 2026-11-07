import os
import boto3
from botocore.config import Config

def load_dotenv(dotenv_path: str = None):
    """표준 .env 파일 읽기 및 환경변수 등록"""
    if dotenv_path is None:
        dotenv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(dotenv_path):
        with open(dotenv_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k_str = k.strip()
                    if k_str not in os.environ:
                        os.environ[k_str] = v.strip()

load_dotenv()

R2_ENDPOINT_URL = os.environ.get("R2_ENDPOINT_URL", "")
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY", "")
R2_BUCKET_NAME = os.environ.get("R2_BUCKET_NAME", "bid-info")


def get_s3_client():
    """Cloudflare R2용 boto3 S3 클라이언트 생성"""
    if os.environ.get("BIDINFO_R2_ENABLED", "0") != "1":
        return None
    endpoint_url = os.environ.get("R2_ENDPOINT_URL", R2_ENDPOINT_URL)
    access_key = os.environ.get("R2_ACCESS_KEY_ID", R2_ACCESS_KEY_ID)
    secret_key = os.environ.get("R2_SECRET_ACCESS_KEY", R2_SECRET_ACCESS_KEY)
    if not access_key or not secret_key:
        return None
    
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4")
    )


def sync_local_to_r2(data_dir):
    """백업·캐시·비밀을 제외하고 현재 로컬 DB와 첨부만 명시적으로 동기화한다."""
    from pathlib import Path
    stats = {"success": 0, "failed": 0}
    base = Path(data_dir)
    for path in base.rglob("*"):
        if not path.is_file() or any(part in ("cache", "backups") for part in path.relative_to(base).parts):
            continue
        if path.suffix.lower() not in (".pdf", ".hwp", ".hwpx", ".docx", ".xlsx", ".zip", ".txt", ".file", ".db"):
            continue
        # sqlite backup으로 쓰기 중인 DB의 일관된 스냅샷을 업로드한다.
        if path.name == "bids_history.db":
            success = upload_db_to_r2(str(path))
        else:
            success = upload_file_to_r2(str(path), "data/" + path.relative_to(base).as_posix())
        stats["success" if success else "failed"] += 1
    return stats


def upload_db_to_r2(local_db_path: str) -> bool:
    """로컬 bids_history.db를 R2 스토리지에 영구 업로드 동기화"""
    s3 = get_s3_client()
    if not s3 or not os.path.exists(local_db_path):
        return False
    try:
        import sqlite3
        import tempfile
        bucket = os.environ.get("R2_BUCKET_NAME", R2_BUCKET_NAME)
        with tempfile.TemporaryDirectory(prefix="bid-db-sync-") as folder:
            snapshot = os.path.join(folder, "bids_history.db")
            with sqlite3.connect(local_db_path) as source, sqlite3.connect(snapshot) as target:
                source.backup(target)
            s3.upload_file(snapshot, bucket, "bids_history.db")
        print("📤 [R2 Storage] bids_history.db 스토리지 영구 보관 업로드 완료!")
        return True
    except Exception as e:
        print(f"❌ [R2 Storage] DB 업로드 실패: {e}")
        return False

def upload_file_to_r2(local_file_path: str, r2_key: str) -> bool:
    """첨부파일(PDF/HWP/ZIP) R2 스토리지에 업로드"""
    s3 = get_s3_client()
    if not s3 or not os.path.exists(local_file_path):
        return False
    try:
        bucket = os.environ.get("R2_BUCKET_NAME", R2_BUCKET_NAME)
        s3.upload_file(local_file_path, bucket, r2_key)
        print(f"📤 [R2 Storage] 첨부파일 영구 업로드 완료: {r2_key}")
        return True
    except Exception as e:
        print(f"❌ [R2 Storage] 첨부파일 업로드 실패 ({r2_key}): {e}")
        return False

def delete_r2_folder(r2_folder_prefix: str) -> bool:
    """R2 스토리지 내의 특정 폴더 경로 및 내부 파일 일괄 삭제"""
    s3 = get_s3_client()
    if not s3:
        return False
    try:
        clean_prefix = (r2_folder_prefix or "").strip("/\\")
        if not clean_prefix:
            return False
        prefix = clean_prefix + "/"
        bucket = os.environ.get("R2_BUCKET_NAME", R2_BUCKET_NAME)
        paginator = s3.get_paginator("list_objects_v2")
        deleted_count = 0
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            contents = page.get("Contents", [])
            if contents:
                delete_keys = [{"Key": obj["Key"]} for obj in contents]
                s3.delete_objects(Bucket=bucket, Delete={"Objects": delete_keys})
                deleted_count += len(delete_keys)
        if deleted_count > 0:
            print(f"🗑️ [R2 Storage] 폴더 및 내부 파일 삭제 완료: {prefix}")
        return True
    except Exception as e:
        print(f"❌ [R2 Storage] 폴더 삭제 실패 ({r2_folder_prefix}): {e}")
        return False
