import base64
import io
import zipfile
from pathlib import Path
from typing import Dict, List, Tuple

import streamlit as st
import streamlit.components.v1 as components
from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader, PdfWriter
from streamlit_sortables import sort_items



IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".img", ".heic", ".helf"}
BLANK_THUMB = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/wwAAl8B9nQ4R0QAAAAASUVORK5CYII="
)

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass

ROTATION_CHOICES = [0, 90, 180, 270]
THUMB_WIDTH = 200  # 미리보기 썸네일 가로 크기 (작게)
THUMB_PREVIEW_WIDTH = 1000  # 오버레이 확대용 최대 가로 크기 (성능 우선)
PAGE_SIZE = 20

thumb_sort_component = components.declare_component(
    "thumb_sort_component", path=str(Path(__file__).parent / "streamlit-thumb-sort" / "build")
)
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

def image_bytes_to_pdf_page(img_bytes: bytes, rotate_deg: int = 0, high_quality: bool = False):
    try:
        im = Image.open(io.BytesIO(img_bytes))
    except UnidentifiedImageError:
        raise ValueError("지원되지 않는 이미지 형식입니다 (.img 파일 내부가 이미지가 아닐 수 있어요)")

    if im.mode in ("RGBA", "P"):
        im = im.convert("RGB")
    else:
        im = im.convert("RGB")

    if rotate_deg % 360 != 0:
        # PIL: 양수는 반시계 회전이므로 시계 방향이 되도록 음수
        im = im.rotate(-rotate_deg, expand=True)

    buf = io.BytesIO()
    save_kwargs = {"format": "PDF"}
    if high_quality:
        # 덜 압축된 JPEG로 포함. PNG 등의 무손실은 자동 처리.
        save_kwargs.update({"quality": 95, "subsampling": 0})
    im.save(buf, **save_kwargs)
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

    try:
        uploaded_zip.seek(0)
    except Exception:
        pass

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


def list_direct_items(
    uploaded_files: List,
    allow_pdfs: bool,
) -> Tuple[List[str], Dict[str, object]]:
    """
    개별 업로드 파일 리스트를 반환.
    - return: (표시명 목록, 표시명 -> UploadedFile 매핑)
    """
    allowed = set(IMG_EXTS)
    if allow_pdfs:
        allowed.add(".pdf")

    fixed_to_file: Dict[str, object] = {}
    fixed_names: List[str] = []

    for f in uploaded_files:
        suf = Path(f.name).suffix.lower()
        if suf not in allowed:
            continue

        fixed = f.name
        if fixed in fixed_to_file:
            stem = Path(fixed).stem
            suf = Path(fixed).suffix
            k = 2
            while f"{stem} ({k}){suf}" in fixed_to_file:
                k += 1
            fixed = f"{stem} ({k}){suf}"

        fixed_to_file[fixed] = f
        fixed_names.append(fixed)

    return fixed_names, fixed_to_file


# -----------------------------
# PDF merging helpers
# -----------------------------


def merge_zip_in_order(
    uploaded_zip,
    ordered_fixed_names: List[str],
    fixed_to_raw: Dict[str, str],
    rotations_images: Dict[str, int] | None = None,
    rotations_pdf_pages: Dict[str, Dict[int, int]] | None = None,
    high_quality_images: bool = False,
) -> bytes:
    """
    ordered_fixed_names 순서대로 zip에서 파일을 읽어 하나의 PDF로 병합.
    """
    writer = PdfWriter()
    rotations_images = rotations_images or {}
    rotations_pdf_pages = rotations_pdf_pages or {}

    try:
        uploaded_zip.seek(0)
    except Exception:
        pass

    with zipfile.ZipFile(uploaded_zip) as zf:
        for fixed in ordered_fixed_names:
            raw = fixed_to_raw[fixed]  # 반드시 raw로 읽어야 안전
            suf = Path(fixed).suffix.lower()

            data = zf.read(raw)

            if suf == ".pdf":
                reader = PdfReader(io.BytesIO(data))
                for idx, page in enumerate(reader.pages, start=1):
                    angle = rotations_pdf_pages.get(fixed, {}).get(idx, 0) or 0
                    if angle % 360 != 0:
                        page.rotate(angle)
                    writer.add_page(page)
            elif suf in IMG_EXTS:
                angle = rotations_images.get(fixed, 0) or 0
                page = image_bytes_to_pdf_page(data, rotate_deg=angle, high_quality=high_quality_images)
                writer.add_page(page)

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def merge_direct_files_in_order(
    ordered_fixed_names: List[str],
    fixed_to_file: Dict[str, object],
    rotations_images: Dict[str, int] | None = None,
    rotations_pdf_pages: Dict[str, Dict[int, int]] | None = None,
    high_quality_images: bool = False,
) -> bytes:
    """
    ordered_fixed_names 순서대로 업로드된 파일을 읽어 하나의 PDF로 병합.
    """
    writer = PdfWriter()
    rotations_images = rotations_images or {}
    rotations_pdf_pages = rotations_pdf_pages or {}

    for fixed in ordered_fixed_names:
        uploaded_file = fixed_to_file[fixed]
        suf = Path(fixed).suffix.lower()

        data = uploaded_file.getvalue()

        if suf == ".pdf":
            reader = PdfReader(io.BytesIO(data))
            for idx, page in enumerate(reader.pages, start=1):
                angle = rotations_pdf_pages.get(fixed, {}).get(idx, 0) or 0
                if angle % 360 != 0:
                    page.rotate(angle)
                writer.add_page(page)
        elif suf in IMG_EXTS:
            angle = rotations_images.get(fixed, 0) or 0
            page = image_bytes_to_pdf_page(data, rotate_deg=angle, high_quality=high_quality_images)
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


def human_size(num_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    size = float(num_bytes)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f}{unit}"
        size /= 1024


def make_thumbnail_data_url(img_bytes: bytes, max_width: int = THUMB_WIDTH, quality: int = 80) -> str:
    try:
        im = Image.open(io.BytesIO(img_bytes))
    except Exception:
        return ""

    im = im.convert("RGB")
    w, h = im.size
    if w > max_width:
        ratio = max_width / float(w)
        new_size = (max_width, int(h * ratio))
        im = im.resize(new_size)

    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=quality, optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/jpeg;base64,{b64}"


def ensure_order(items: List[str], session_key: str) -> List[str]:
    saved = st.session_state.get(session_key, [])
    ordered = [x for x in saved if x in items]
    for x in items:
        if x not in ordered:
            ordered.append(x)
    st.session_state[session_key] = ordered
    return ordered


def build_thumb_items_from_zip(
    ordered_names: List[str],
    fixed_to_raw: Dict[str, str],
    uploaded_zip,
    thumb_width: int,
    preview_width: int,
    cache_key_prefix: str,
) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    cache = st.session_state.setdefault(f"{cache_key_prefix}_thumb_cache", {})
    try:
        uploaded_zip.seek(0)
    except Exception:
        pass
    with zipfile.ZipFile(uploaded_zip) as zf:
        for name in ordered_names:
            raw = fixed_to_raw.get(name)
            if raw is None:
                continue
            if name in cache:
                items.append(cache[name])
                continue
            data = zf.read(raw)
            suf = Path(name).suffix.lower()
            if suf in IMG_EXTS:
                thumb = make_thumbnail_data_url(data, max_width=thumb_width, quality=70)
                full = make_thumbnail_data_url(data, max_width=preview_width, quality=85)
            else:
                thumb = BLANK_THUMB
                full = BLANK_THUMB
            cache[name] = {"id": name, "label": name, "thumb": thumb, "full": full}
            items.append(cache[name])
    return items


def build_thumb_items_from_direct(
    ordered_names: List[str],
    fixed_to_file: Dict[str, object],
    thumb_width: int,
    preview_width: int,
    cache_key_prefix: str,
) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    cache = st.session_state.setdefault(f"{cache_key_prefix}_thumb_cache", {})
    for name in ordered_names:
        if name in cache:
            items.append(cache[name])
            continue
        f = fixed_to_file.get(name)
        if not f:
            continue
        data = f.getvalue()
        suf = Path(name).suffix.lower()
        if suf in IMG_EXTS:
            thumb = make_thumbnail_data_url(data, max_width=thumb_width, quality=70)
            full = make_thumbnail_data_url(data, max_width=preview_width, quality=85)
        else:
            thumb = BLANK_THUMB
            full = BLANK_THUMB
        cache[name] = {"id": name, "label": name, "thumb": thumb, "full": full}
        items.append(cache[name])
    return items


def show_pdf_preview(pdf_bytes: bytes, key_prefix: str, height: int = 720) -> None:
    """
    Render PDF bytes inline without saving to disk.
    """
    b64_pdf = base64.b64encode(pdf_bytes).decode("utf-8")
    # 일부 브라우저/확장프로그램이 data: PDF를 차단하므로,
    # 브라우저 안에서 Blob URL을 생성해 안전하게 embed로 렌더링한다.
    components.html(
        f"""
        <div style="border:1px solid #ddd; border-radius:6px; overflow:hidden">
            <embed id="pdf-embed" type="application/pdf" width="100%" height="{height}" />
            <div style="padding:8px; font-size:13px; color:#444;">
                미리보기가 안 보이면
                <a id="pdf-fallback" download="preview.pdf">여기</a>
                를 눌러 새 탭에서 열어주세요.
            </div>
        </div>
        <script>
            (function() {{
                try {{
                    const b64 = "{b64_pdf}";
                    const byteChars = atob(b64);
                    const byteNumbers = new Array(byteChars.length);
                    for (let i = 0; i < byteChars.length; i++) {{
                        byteNumbers[i] = byteChars.charCodeAt(i);
                    }}
                    const byteArray = new Uint8Array(byteNumbers);
                    const blob = new Blob([byteArray], {{ type: "application/pdf" }});
                    const url = URL.createObjectURL(blob);
                    document.getElementById("pdf-embed").src = url;
                    const link = document.getElementById("pdf-fallback");
                    link.href = url;
                }} catch (err) {{
                    console.error("PDF preview failed", err);
                }}
            }})();
        </script>
        """,
        height=height + 48,
        scrolling=True,
    )


def compress_pdf_lossless(pdf_bytes: bytes) -> bytes:
    """
    Try to reduce PDF size by compressing content streams without touching images.
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))
    writer = PdfWriter()
    for page in reader.pages:
        try:
            page.compress_content_streams()
        except Exception:
            # 일부 페이지가 스트림이 없을 수 있음
            pass
        writer.add_page(page)

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def render_rotation_controls_for_zip(
    fixed_names: List[str],
    fixed_to_raw: Dict[str, str],
    uploaded_zip,
    state_key: str,
) -> Dict[str, int]:
    rotations = st.session_state.setdefault(state_key, {})
    try:
        uploaded_zip.seek(0)
    except Exception:
        pass
    with zipfile.ZipFile(uploaded_zip) as zf:
        for idx, fixed in enumerate(fixed_names, start=1):
            if Path(fixed).suffix.lower() not in IMG_EXTS:
                continue
            try:
                data = zf.read(fixed_to_raw[fixed])
            except KeyError:
                continue
            col1, col2 = st.columns([4, 1])
            with col1:
                st.caption(f"{idx}. {fixed}")
                st.image(data, width=THUMB_WIDTH)
            with col2:
                default_rot = rotations.get(fixed, 0) or 0
                idx = ROTATION_CHOICES.index(default_rot) if default_rot in ROTATION_CHOICES else 0
                rotations[fixed] = st.selectbox(
                    "회전(시계 방향)",
                    ROTATION_CHOICES,
                    index=idx,
                    key=f"{state_key}_{fixed}",
                )
    return rotations


def render_rotation_controls_for_direct(
    fixed_names: List[str],
    fixed_to_file: Dict[str, object],
    state_key: str,
) -> Dict[str, int]:
    rotations = st.session_state.setdefault(state_key, {})
    for idx, fixed in enumerate(fixed_names, start=1):
        if Path(fixed).suffix.lower() not in IMG_EXTS:
            continue
        data = fixed_to_file[fixed].getvalue()
        col1, col2 = st.columns([4, 1])
        with col1:
            st.caption(f"{idx}. {fixed}")
            st.image(data, width=THUMB_WIDTH)
        with col2:
            default_rot = rotations.get(fixed, 0) or 0
            idx = ROTATION_CHOICES.index(default_rot) if default_rot in ROTATION_CHOICES else 0
            rotations[fixed] = st.selectbox(
                "회전(시계 방향)",
                ROTATION_CHOICES,
                index=idx,
                key=f"{state_key}_{fixed}",
            )
    return rotations


def render_pdf_page_rotations_for_zip(
    fixed_names: List[str],
    fixed_to_raw: Dict[str, str],
    uploaded_zip,
    state_key: str,
) -> Dict[str, Dict[int, int]]:
    rotations = st.session_state.setdefault(state_key, {})
    try:
        uploaded_zip.seek(0)
    except Exception:
        pass
    with zipfile.ZipFile(uploaded_zip) as zf:
        for fixed in fixed_names:
            if Path(fixed).suffix.lower() != ".pdf":
                continue
            try:
                data = zf.read(fixed_to_raw[fixed])
            except KeyError:
                continue
            reader = PdfReader(io.BytesIO(data))
            num_pages = len(reader.pages)
            st.write(f"{fixed} (총 {num_pages}p)")
            cols = st.columns(4)
            file_map = rotations.get(fixed, {})
            for idx in range(1, num_pages + 1):
                default_rot = file_map.get(idx, 0) or 0
                default_idx = ROTATION_CHOICES.index(default_rot) if default_rot in ROTATION_CHOICES else 0
                col = cols[(idx - 1) % len(cols)]
                with col:
                    file_map[idx] = st.selectbox(
                        f"p.{idx}",
                        ROTATION_CHOICES,
                        index=default_idx,
                        key=f"{state_key}_{fixed}_p{idx}",
                    )
            rotations[fixed] = file_map
    return rotations


def render_pdf_page_rotations_for_direct(
    fixed_names: List[str],
    fixed_to_file: Dict[str, object],
    state_key: str,
) -> Dict[str, Dict[int, int]]:
    rotations = st.session_state.setdefault(state_key, {})
    for fixed in fixed_names:
        if Path(fixed).suffix.lower() != ".pdf":
            continue
        data = fixed_to_file[fixed].getvalue()
        reader = PdfReader(io.BytesIO(data))
        num_pages = len(reader.pages)
        st.write(f"{fixed} (총 {num_pages}p)")
        cols = st.columns(4)
        file_map = rotations.get(fixed, {})
        for idx in range(1, num_pages + 1):
            default_rot = file_map.get(idx, 0) or 0
            default_idx = ROTATION_CHOICES.index(default_rot) if default_rot in ROTATION_CHOICES else 0
            col = cols[(idx - 1) % len(cols)]
            with col:
                file_map[idx] = st.selectbox(
                    f"p.{idx}",
                    ROTATION_CHOICES,
                    index=default_idx,
                    key=f"{state_key}_{fixed}_p{idx}",
                )
        rotations[fixed] = file_map
    return rotations


# -----------------------------
# Streamlit app
# -----------------------------
st.set_page_config(page_title="Zip → Drag Sort → PDF (Stable)", layout="wide")
st.title("Zip 업로드 → 드래그 정렬 → PDF 병합 (최종 안정 버전)")

tab0, tab1, tab2, tab3 = st.tabs(
    ["설명", "1) 이미지 zip → PDF", "2) PDF+이미지 zip → PDF", "3) PDF/이미지 직접 업로드"]
)

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

### 현재 동작 옵션
- **정렬 방식**: 기본 텍스트 목록(가벼움), 썸네일 모드 전환 가능.
- **페이징**: 20개씩 나눠 정렬.
- **회전/미리보기**: 임시 비활성화(회전 적용 안 함).
- **고화질 옵션**: 체크 시 이미지 페이지를 덜 압축해 포함(용량 증가).

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

### 현재 동작 옵션
- **정렬 방식**: 기본 텍스트 목록(가벼움), 썸네일 모드 전환 가능(PDF는 회색 카드).
- **페이징**: 20개씩 나눠 정렬.
- **회전/미리보기**: 임시 비활성화(회전 적용 안 함).
- **고화질 옵션**: 체크 시 이미지 페이지를 덜 압축해 포함(용량 증가).

---

## 지원 파일 형식
- 이미지: **.jpg, .jpeg, .png, .webp, .tif, .tiff**
- 문서: **.pdf** (탭 2에서만)

---

## 탭 3: PDF/이미지 직접 업로드
- **정렬 방식**: 기본 텍스트 목록(가벼움), 썸네일 모드 전환 가능(PDF는 회색 카드).
- **페이징**: 20개씩 나눠 정렬.
- **회전/미리보기**: 임시 비활성화(회전 적용 안 함).
- **고화질 옵션**: 체크 시 이미지 페이지를 덜 압축해 포함(용량 증가).

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

            high_quality_images = st.checkbox(
                "이미지를 고화질로 포함 (용량 증가)", value=False, key="hq_images_zip_only"
            )

            # rotations 블록 임시 비활성화
            rotations = {}

            # 순서 정렬 모드 선택
            use_text_mode = st.checkbox("텍스트 목록으로 정렬(썸네일 끄기)", value=True, key="text_mode_images")

            ordered = ensure_order(fixed_names, session_key="order_images")
            page = st.session_state.get("page_images", 1)
            total_pages = max(1, (len(ordered) + PAGE_SIZE - 1) // PAGE_SIZE)
            page = st.number_input("페이지", min_value=1, max_value=total_pages, value=page, step=1, key="page_input_images")
            st.session_state["page_images"] = page
            start = (page - 1) * PAGE_SIZE
            end = start + PAGE_SIZE
            page_items = ordered[start:end]

            if use_text_mode:
                ordered_page = drag_sort_list_ui("이미지 순서 정렬(텍스트)", page_items, key=f"sort_images_page_{page}")
                ordered = ordered[:start] + ordered_page + ordered[end:]
                st.session_state["order_images"] = ordered
                st.caption("텍스트 리스트에서 드래그하여 순서를 바꾸세요.")
            else:
                thumb_items = build_thumb_items_from_zip(
                    page_items,
                    fixed_to_raw,
                    uploaded,
                    thumb_width=THUMB_WIDTH,
                    preview_width=THUMB_PREVIEW_WIDTH,
                    cache_key_prefix="images",
                )
                new_order = thumb_sort_component(items=thumb_items, key=f"thumb_sort_images_{page}")
                if new_order is not None and len(new_order) == len(page_items):
                    # 페이지 내 순서를 반영
                    page_ordered = [x for x in new_order if x in page_items]
                    ordered = ordered[:start] + page_ordered + ordered[end:]
                    st.session_state["order_images"] = ordered
                st.caption(f"썸네일을 드래그해서 순서를 바꿔보세요. (페이지 {page}/{total_pages})")

            if st.button("정렬된 순서로 PDF 만들기", key="make_pdf_images"):
                try:
                    pdf_bytes = merge_zip_in_order(
                        uploaded,
                        ordered,
                        fixed_to_raw,
                        rotations_images=rotations,
                        rotations_pdf_pages={},
                        high_quality_images=high_quality_images,
                    )
                    st.session_state["pdf_images_bytes"] = pdf_bytes
                    st.download_button(
                        "PDF 다운로드",
                        data=pdf_bytes,
                        file_name=pdf_name,   # ✅ 여기
                        mime="application/pdf",
                        key="download_images_pdf",
                    )
                    with st.expander("병합 PDF 미리보기 (저장 전)", expanded=False):
                        show_pdf_preview(pdf_bytes, key_prefix="images")
                    with st.expander("PDF 용량 줄이기 (무손실 시도)", expanded=False):
                        if st.button("용량 줄이기 실행", key="compress_images"):
                            compressed = compress_pdf_lossless(pdf_bytes)
                            st.write(f"원본: {human_size(len(pdf_bytes))} → 압축: {human_size(len(compressed))}")
                            st.download_button(
                                "압축된 PDF 다운로드",
                                data=compressed,
                                file_name=pdf_name,
                                mime="application/pdf",
                                key="download_images_pdf_compressed",
                            )
                            show_pdf_preview(compressed, key_prefix="images_compressed")
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

            high_quality_images = st.checkbox(
                "이미지를 고화질로 포함 (용량 증가)", value=False, key="hq_images_zip_mixed"
            )

            rotations = {}
            pdf_rotations = {}

            use_text_mode = st.checkbox("텍스트 목록으로 정렬(썸네일 끄기)", value=True, key="text_mode_mixed")

            ordered = ensure_order(fixed_names, session_key="order_mixed")
            page = st.session_state.get("page_mixed", 1)
            total_pages = max(1, (len(ordered) + PAGE_SIZE - 1) // PAGE_SIZE)
            page = st.number_input("페이지", min_value=1, max_value=total_pages, value=page, step=1, key="page_input_mixed")
            st.session_state["page_mixed"] = page
            start = (page - 1) * PAGE_SIZE
            end = start + PAGE_SIZE
            page_items = ordered[start:end]

            if use_text_mode:
                ordered_page = drag_sort_list_ui("PDF + 이미지 순서 정렬(텍스트)", page_items, key=f"sort_mixed_page_{page}")
                ordered = ordered[:start] + ordered_page + ordered[end:]
                st.session_state["order_mixed"] = ordered
                st.caption("텍스트 리스트에서 드래그하여 순서를 바꾸세요.")
            else:
                thumb_items = build_thumb_items_from_zip(
                    page_items,
                    fixed_to_raw,
                    uploaded,
                    thumb_width=THUMB_WIDTH,
                    preview_width=THUMB_PREVIEW_WIDTH,
                    cache_key_prefix="mixed",
                )
                new_order = thumb_sort_component(items=thumb_items, key=f"thumb_sort_mixed_{page}")
                if new_order is not None and len(new_order) == len(page_items):
                    page_ordered = [x for x in new_order if x in page_items]
                    ordered = ordered[:start] + page_ordered + ordered[end:]
                    st.session_state["order_mixed"] = ordered
                st.caption(f"썸네일(또는 카드)을 드래그해서 순서를 바꿔보세요. (페이지 {page}/{total_pages})")

            pdf_name_input = st.text_input("다운로드 PDF 파일명", value="merged.pdf", key="pdf_name_mixed")
            pdf_name = normalize_pdf_name(pdf_name_input)

            if st.button("정렬된 순서로 하나의 PDF 만들기", key="make_pdf_mixed"):
                try:
                    pdf_bytes = merge_zip_in_order(
                        uploaded,
                        ordered,
                        fixed_to_raw,
                        rotations_images=rotations,
                        rotations_pdf_pages=pdf_rotations,
                        high_quality_images=high_quality_images,
                    )
                    st.session_state["pdf_mixed_bytes"] = pdf_bytes
                    st.download_button(
                        "PDF 다운로드",
                        data=pdf_bytes,
                        file_name=pdf_name,   # ✅ 여기
                        mime="application/pdf",
                        key="download_mixed_pdf",
                    )
                    with st.expander("병합 PDF 미리보기 (저장 전)", expanded=False):
                        show_pdf_preview(pdf_bytes, key_prefix="mixed")
                    with st.expander("PDF 용량 줄이기 (무손실 시도)", expanded=False):
                        if st.button("용량 줄이기 실행", key="compress_mixed"):
                            compressed = compress_pdf_lossless(pdf_bytes)
                            st.write(f"원본: {human_size(len(pdf_bytes))} → 압축: {human_size(len(compressed))}")
                            st.download_button(
                                "압축된 PDF 다운로드",
                                data=compressed,
                                file_name=pdf_name,
                                mime="application/pdf",
                                key="download_mixed_pdf_compressed",
                            )
                            show_pdf_preview(compressed, key_prefix="mixed_compressed")
                except ValueError as e:
                    st.error(f"PDF 생성 중 오류가 발생했습니다.\n\n{e}")

                except Exception as e:
                    st.error("알 수 없는 오류가 발생했습니다.")
                    st.exception(e)  # 개발 중에만 사용 (스택트레이스 표시)

with tab3:
    st.subheader("zip 없이 PDF/이미지 직접 업로드")
    st.caption("여러 개의 PDF/이미지 파일을 선택해서 바로 정렬하고 병합할 수 있습니다.")

    direct_types = [ext.lstrip(".") for ext in sorted(list(IMG_EXTS | {".pdf"}))]
    uploaded_files = st.file_uploader(
        "PDF/이미지 파일 업로드 (여러 개 선택 가능)",
        type=direct_types,
        accept_multiple_files=True,
        key="direct_files",
    )

    if uploaded_files:
        fixed_names, fixed_to_file = list_direct_items(uploaded_files, allow_pdfs=True)

        if not fixed_names:
            st.error("업로드된 파일에서 PDF/이미지를 찾지 못했어요.")
        else:
            st.write("파일 개수:", len(fixed_names))
            with st.expander("파일 목록 보기", expanded=False):
                for x in fixed_names:
                    st.write(nice_label(x))

            high_quality_images = st.checkbox(
                "이미지를 고화질로 포함 (용량 증가)", value=False, key="hq_images_direct"
            )

            rotations = {}
            pdf_rotations = {}

            use_text_mode = st.checkbox("텍스트 목록으로 정렬(썸네일 끄기)", value=True, key="text_mode_direct")

            ordered = ensure_order(fixed_names, session_key="order_direct")
            page = st.session_state.get("page_direct", 1)
            total_pages = max(1, (len(ordered) + PAGE_SIZE - 1) // PAGE_SIZE)
            page = st.number_input("페이지", min_value=1, max_value=total_pages, value=page, step=1, key="page_input_direct")
            st.session_state["page_direct"] = page
            start = (page - 1) * PAGE_SIZE
            end = start + PAGE_SIZE
            page_items = ordered[start:end]

            if use_text_mode:
                ordered_page = drag_sort_list_ui("PDF + 이미지 순서 정렬(텍스트)", page_items, key=f"sort_direct_page_{page}")
                ordered = ordered[:start] + ordered_page + ordered[end:]
                st.session_state["order_direct"] = ordered
                st.caption("텍스트 리스트에서 드래그하여 순서를 바꾸세요.")
            else:
                thumb_items = build_thumb_items_from_direct(
                    page_items,
                    fixed_to_file,
                    thumb_width=THUMB_WIDTH,
                    preview_width=THUMB_PREVIEW_WIDTH,
                    cache_key_prefix="direct",
                )
                new_order = thumb_sort_component(items=thumb_items, key=f"thumb_sort_direct_{page}")
                if new_order is not None and len(new_order) == len(page_items):
                    page_ordered = [x for x in new_order if x in page_items]
                    ordered = ordered[:start] + page_ordered + ordered[end:]
                    st.session_state["order_direct"] = ordered
                st.caption(f"썸네일(또는 카드)을 드래그해서 순서를 바꿔보세요. (페이지 {page}/{total_pages})")

            pdf_name_input = st.text_input("다운로드 PDF 파일명", value="merged.pdf", key="pdf_name_direct")
            pdf_name = normalize_pdf_name(pdf_name_input)

            if st.button("정렬된 순서로 하나의 PDF 만들기", key="make_pdf_direct"):
                try:
                    pdf_bytes = merge_direct_files_in_order(
                        ordered,
                        fixed_to_file,
                        rotations_images=rotations,
                        rotations_pdf_pages=pdf_rotations,
                        high_quality_images=high_quality_images,
                    )
                    st.session_state["pdf_direct_bytes"] = pdf_bytes
                    st.download_button(
                        "PDF 다운로드",
                        data=pdf_bytes,
                        file_name=pdf_name,
                        mime="application/pdf",
                        key="download_direct_pdf",
                    )
                    with st.expander("병합 PDF 미리보기 (저장 전)", expanded=False):
                        show_pdf_preview(pdf_bytes, key_prefix="direct")
                    with st.expander("PDF 용량 줄이기 (무손실 시도)", expanded=False):
                        if st.button("용량 줄이기 실행", key="compress_direct"):
                            compressed = compress_pdf_lossless(pdf_bytes)
                            st.write(f"원본: {human_size(len(pdf_bytes))} → 압축: {human_size(len(compressed))}")
                            st.download_button(
                                "압축된 PDF 다운로드",
                                data=compressed,
                                file_name=pdf_name,
                                mime="application/pdf",
                                key="download_direct_pdf_compressed",
                            )
                            show_pdf_preview(compressed, key_prefix="direct_compressed")
                except ValueError as e:
                    st.error(f"PDF 생성 중 오류가 발생했습니다.\n\n{e}")

                except Exception as e:
                    st.error("알 수 없는 오류가 발생했습니다.")
                    st.exception(e)  # 개발 중에만 사용 (스택트레이스 표시)
