import os
import boto3
from botocore.config import Config

# R2 환경 변수 또는 secrets에서 설정 읽기
R2_ENDPOINT_URL = os.environ.get("R2_ENDPOINT_URL", "https://ab2ae35de3e5e4fdf200ba9417f9419f.r2.cloudflarestorage.com")
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY", "")
R2_BUCKET_NAME = os.environ.get("R2_BUCKET_NAME", "bid-info")

def get_s3_client():
    """Cloudflare R2용 boto3 S3 클라이언트 생성"""
    if not R2_ACCESS_KEY_ID or not R2_SECRET_ACCESS_KEY:
        return None
    
    return boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT_URL,
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        config=Config(signature_version="s3v4")
    )

def download_db_from_r2(local_db_path: str) -> bool:
    """R2 스토리지에서 bids_history.db 동기화 다운로드"""
    s3 = get_s3_client()
    if not s3:
        return False
    try:
        os.makedirs(os.path.dirname(local_db_path), exist_ok=True)
        s3.download_file(R2_BUCKET_NAME, "bids_history.db", local_db_path)
        print("📥 [R2 Storage] bids_history.db 성공적으로 최신 동기화 완료!")
        return True
    except Exception as e:
        print(f"⚠️ [R2 Storage] DB 다운로드 시도 (스토리지 신규인 경우 정상): {e}")
        return False

def upload_db_to_r2(local_db_path: str) -> bool:
    """로컬 bids_history.db를 R2 스토리지에 영구 업로드 동기화"""
    s3 = get_s3_client()
    if not s3 or not os.path.exists(local_db_path):
        return False
    try:
        s3.upload_file(local_db_path, R2_BUCKET_NAME, "bids_history.db")
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
        s3.upload_file(local_file_path, R2_BUCKET_NAME, r2_key)
        print(f"📤 [R2 Storage] 첨부파일 영구 업로드 완료: {r2_key}")
        return True
    except Exception as e:
        print(f"❌ [R2 Storage] 첨부파일 업로드 실패 ({r2_key}): {e}")
        return False
