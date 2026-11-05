import requests
import streamlit as st


def friendly_error(error):
    """사용자에게 보여줄 한 줄 안내와, 상세 내용을 따로 보여줄지 여부를 돌려준다."""
    if isinstance(error, (requests.ConnectionError, requests.Timeout)):
        return "사이트에 연결하지 못했습니다. 네트워크와 사이트 주소를 확인한 뒤 다시 시도해 주세요.", True
    if isinstance(error, OSError):
        return "파일을 읽거나 쓰지 못했습니다. 저장 공간과 폴더 권한을 확인해 주세요.", True
    if isinstance(error, (ValueError, RuntimeError)):
        return str(error), False
    return f"예상하지 못한 오류가 발생했습니다({type(error).__name__}). 같은 문제가 반복되면 상세 내용을 확인해 주세요.", True


def show_error(error):
    message, has_detail = friendly_error(error)
    st.error(message)
    if has_detail:
        with st.expander("상세 오류"):
            st.code(f"{type(error).__name__}: {error}")
