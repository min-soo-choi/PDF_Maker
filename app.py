import io
import zipfile
from pathlib import Path
from typing import Dict, List, Tuple

import streamlit as st
from PIL import Image
from pypdf import PdfReader, PdfWriter
from streamlit_sortables import sort_items
from PIL import UnidentifiedImageError



IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".img"}


# -----------------------------
# Zip filename decoding helpers
# -----------------------------
def fix_zip_name(raw_name: str) -> str:
    """
    zipfile이 파일명을 cp437로 잘못 해석했을 때 흔히 쓰는 복구 시도.
    (macOS Finder zip에서 자주 발생)
    """
    try:
        return raw_name.encode("cp437").decode("utf-8")
    except Exception:
        return raw_name

def image_bytes_to_pdf_page(img_bytes: bytes):
    try:
        im = Image.open(io.BytesIO(img_bytes))
    except UnidentifiedImageError:
        raise ValueError("지원되지 않는 이미지 형식입니다 (.img 파일 내부가 이미지가 아닐 수 있어요)")

    if im.mode in ("RGBA", "P"):
        im = im.convert("RGB")
    else:
        im = im.convert("RGB")

    buf = io.BytesIO()
    im.save(buf, format="PDF")
    buf.seek(0)

    reader = PdfReader(buf)
    return reader.pages[0]


def list_zip_items(
    uploaded_zip,
    allow_pdfs: bool,
) -> Tuple[List[str], Dict[str, str]]:
    """
    zip 내 파일 목록을 (표시명 fixed_name) 기준으로 반환.
    - return: (fixed_names_sorted, fixed_to_raw_map)
    """
    allowed = set(IMG_EXTS)
    if allow_pdfs:
        allowed.add(".pdf")

    fixed_to_raw: Dict[str, str] = {}
    fixed_names: List[str] = []

    with zipfile.ZipFile(uploaded_zip) as zf:
        for info in zf.infolist():
            raw = info.filename  # zip 내부 경로 (zip이 가진 원본 문자열)
            # 폴더 엔트리 스킵
            if raw.endswith("/"):
                continue

            fixed = fix_zip_name(raw)

            # macOS junk 스킵
            if "__MACOSX" in fixed or fixed.startswith("."):
                continue

            suf = Path(fixed).suffix.lower()
            if suf not in allowed:
                continue

            # 표시명 충돌 방지(동일 fixed가 생기는 경우 대비)
            if fixed in fixed_to_raw:
                # 동일한 표시명이면 뒤에 짧게 disambiguation
                stem = fixed
                k = 2
                while f"{stem} ({k})" in fixed_to_raw:
                    k += 1
                fixed = f"{stem} ({k})"

            fixed_to_raw[fixed] = raw
            fixed_names.append(fixed)

    fixed_names.sort(key=lambda x: x.lower())
    return fixed_names, fixed_to_raw


# -----------------------------
# PDF merging helpers
# -----------------------------
def image_bytes_to_pdf_page(img_bytes: bytes):
    """
    이미지 bytes -> 1페이지 PDF page로 변환
    """
    im = Image.open(io.BytesIO(img_bytes))
    if im.mode in ("RGBA", "P"):
        im = im.convert("RGB")
    else:
        im = im.convert("RGB")

    buf = io.BytesIO()
    im.save(buf, format="PDF")
    buf.seek(0)

    reader = PdfReader(buf)
    return reader.pages[0]


def merge_zip_in_order(
    uploaded_zip,
    ordered_fixed_names: List[str],
    fixed_to_raw: Dict[str, str],
) -> bytes:
    """
    ordered_fixed_names 순서대로 zip에서 파일을 읽어 하나의 PDF로 병합.
    """
    writer = PdfWriter()

    with zipfile.ZipFile(uploaded_zip) as zf:
        for fixed in ordered_fixed_names:
            raw = fixed_to_raw[fixed]  # 반드시 raw로 읽어야 안전
            suf = Path(fixed).suffix.lower()

            data = zf.read(raw)

            if suf == ".pdf":
                reader = PdfReader(io.BytesIO(data))
                for page in reader.pages:
                    writer.add_page(page)
            elif suf in IMG_EXTS:
                page = image_bytes_to_pdf_page(data)
                writer.add_page(page)

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


# -----------------------------
# UI helpers
# -----------------------------
def drag_sort_list_ui(title: str, items: List[str], key: str) -> List[str]:
    st.subheader(title)
    st.caption("드래그로 순서를 바꾼 뒤, 아래 버튼으로 PDF를 생성하세요.")

    # Streamlit-sortables: multi_containers=False이면 list[str]만 받음
    sorted_items = sort_items(items, multi_containers=False, key=key)
    return sorted_items


def nice_label(s: str) -> str:
    """
    긴 경로 표시를 보기 좋게 줄이고 싶으면 여기서 조정.
    현재는 zip 내부 경로를 그대로 보여줌(가장 명확/충돌 적음).
    """
    return s

def normalize_pdf_name(name: str) -> str:
    name = name.strip()
    if not name:
        return "merged.pdf"
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    return name



# -----------------------------
# Streamlit app
# -----------------------------
st.set_page_config(page_title="Zip → Drag Sort → PDF (Stable)", layout="wide")
st.title("Zip 업로드 → 드래그 정렬 → PDF 병합 (최종 안정 버전)")

tab0, tab1, tab2 = st.tabs(["설명", "1) 이미지 zip → PDF", "2) PDF+이미지 zip → PDF"])

with tab0:
    st.header("앱 설명 / 사용 방법")

    st.markdown("""
### 이 앱으로 할 수 있는 것
- **zip 파일을 업로드**하면, zip 안의 파일 목록을 불러옵니다.
- 목록을 **드래그로 원하는 순서로 정렬**할 수 있습니다.
- 정렬된 순서대로 파일을 **하나의 PDF로 병합**해서 다운로드합니다.

---

## 탭 1: 이미지 zip → PDF
### 사용 순서
1. **이미지만 들어있는 zip**을 업로드합니다. (jpg/png/webp/tif 지원)
2. 파일 목록이 나오면 **드래그로 페이지 순서를 정렬**합니다.
3. **PDF 파일명을 입력**한 뒤, “PDF 만들기” 버튼을 누릅니다.
4. 생성된 **PDF를 다운로드**합니다.

### 동작 방식
- 각 이미지 파일은 **PDF의 1페이지**로 변환됩니다.
- 정렬된 순서대로 페이지가 붙어서 최종 PDF가 만들어집니다.

---

## 탭 2: PDF + 이미지 zip → PDF
### 사용 순서
1. **PDF와 이미지가 섞인 zip**을 업로드합니다.
2. 목록을 **드래그로 전체 순서**를 정합니다.
3. **PDF 파일명 입력 → PDF 만들기 → 다운로드** 순서로 진행합니다.

### 동작 방식
- zip 안의 **PDF는 페이지 단위로 그대로** 병합됩니다.
- **이미지는 1페이지 PDF로 변환**된 뒤 병합됩니다.
- 그래서 “PDF 중간에 이미지 페이지 끼워넣기” 같은 것도 가능합니다.

---

## 지원 파일 형식
- 이미지: **.jpg, .jpeg, .png, .webp, .tif, .tiff**
- 문서: **.pdf** (탭 2에서만)

---

## 파일명(한글) 깨짐 관련
- 일부 환경(특히 macOS에서 만든 zip)에서 파일명이 깨질 수 있는데,
  이 앱은 **zip 파일명 인코딩을 복구**해서 최대한 정상적으로 표시/처리합니다.
- 그래도 문제가 있으면 zip을 만드는 방식(압축 툴)에 따라 달라질 수 있습니다.

---

## 주의사항 / 팁
- 이미지 해상도가 매우 크면 결과 PDF 용량이 커질 수 있습니다.
- 파일 순서가 중요한 경우, 드래그 정렬 후 **목록을 다시 한번 확인**하고 생성하세요.
- zip 내부에 `__MACOSX` 같은 시스템 폴더가 있어도 자동으로 무시합니다.
""")


with tab1:
    uploaded = st.file_uploader("이미지만 들어있는 zip 업로드", type=["zip"], key="zip_images")
    pdf_name_input = st.text_input("다운로드 PDF 파일명", value="학교_1_merged.pdf", key="pdf_name_images")
    pdf_name = normalize_pdf_name(pdf_name_input)

    if uploaded:
        fixed_names, fixed_to_raw = list_zip_items(uploaded, allow_pdfs=False)

        if not fixed_names:
            st.error("zip에서 이미지 파일(jpg/png/webp/tif)을 찾지 못했어요.")
        else:
            # 화면 표시용으로 라벨을 바꿀 수도 있지만, 여기선 안정 위해 fixed_names 그대로 정렬
            # (만약 label을 바꾸면, 다시 ID 매핑이 필요해져서 KeyError 위험이 커짐)
            st.write("파일 개수:", len(fixed_names))
            with st.expander("파일 목록 보기", expanded=False):
                for x in fixed_names:
                    st.write(nice_label(x))

            ordered = drag_sort_list_ui("이미지 순서 정렬", fixed_names, key="sort_images")

            if st.button("정렬된 순서로 PDF 만들기", key="make_pdf_images"):
                try:
                    pdf_bytes = merge_zip_in_order(uploaded, ordered, fixed_to_raw)
                    st.download_button(
                        "PDF 다운로드",
                        data=pdf_bytes,
                        file_name=pdf_name,   # ✅ 여기
                        mime="application/pdf",
                        key="download_images_pdf",
                    )
                except ValueError as e:
                    st.error(f"PDF 생성 중 오류가 발생했습니다.\n\n{e}")

                except Exception as e:
                    st.error("알 수 없는 오류가 발생했습니다.")
                    st.exception(e)  # 개발 중에만 사용 (스택트레이스 표시)
with tab2:
    uploaded = st.file_uploader("PDF와 이미지가 섞인 zip 업로드", type=["zip"], key="zip_mixed")

    if uploaded:
        fixed_names, fixed_to_raw = list_zip_items(uploaded, allow_pdfs=True)

        if not fixed_names:
            st.error("zip에서 PDF/이미지 파일을 찾지 못했어요.")
        else:
            st.write("파일 개수:", len(fixed_names))
            with st.expander("파일 목록 보기", expanded=False):
                for x in fixed_names:
                    st.write(nice_label(x))

            ordered = drag_sort_list_ui("PDF + 이미지 순서 정렬", fixed_names, key="sort_mixed")

            pdf_name_input = st.text_input("다운로드 PDF 파일명", value="merged.pdf", key="pdf_name_mixed")
            pdf_name = normalize_pdf_name(pdf_name_input)

            if st.button("정렬된 순서로 하나의 PDF 만들기", key="make_pdf_mixed"):
                try:
                    pdf_bytes = merge_zip_in_order(uploaded, ordered, fixed_to_raw)
                    st.download_button(
                        "PDF 다운로드",
                        data=pdf_bytes,
                        file_name=pdf_name,   # ✅ 여기
                        mime="application/pdf",
                        key="download_mixed_pdf",
                    )
                except ValueError as e:
                    st.error(f"PDF 생성 중 오류가 발생했습니다.\n\n{e}")

                except Exception as e:
                    st.error("알 수 없는 오류가 발생했습니다.")
                    st.exception(e)  # 개발 중에만 사용 (스택트레이스 표시)

