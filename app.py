"""쇼핑리스트 Streamlit 앱."""

import html
import re
import uuid
from datetime import datetime

import streamlit as st

from storage import NAME_MAX_LEN, QTY_MAX, QTY_MIN, load_items, update_items

st.set_page_config(page_title="쇼핑리스트", page_icon="🛒")

FILTER_OPTIONS = ["전체", "미완료", "완료"]
# 한 화면에 그리는 최대 항목 수. Streamlit은 요소 하나를 만들 때마다 비용이 들어서
# (행당 약 10개 요소) 500개를 한 번에 그리면 실행 한 번에 3~4초가 걸린다. (PRD 7장: 1초 이내)
PAGE_SIZE = 50


# ---------------------------------------------------------------------------
# 상태 초기화 / 메시지
# ---------------------------------------------------------------------------
def init_state() -> None:
    """매 실행마다 파일에서 최신 목록을 읽는다. (다른 탭의 변경도 반영됨)"""
    items, warning = load_items()
    st.session_state["items"] = items
    st.session_state.setdefault("editing_id", None)
    st.session_state.setdefault("edit_draft", None)  # 편집 중인 값 {"name", "quantity"}
    st.session_state.setdefault("flash", [])  # 다음 렌더링 때 보여줄 (종류, 문구)
    st.session_state.setdefault("new_name", "")
    st.session_state.setdefault("new_qty", 1)
    st.session_state.setdefault("page", 0)
    if warning:
        flash("warning", warning)
    # 편집 중이던 항목이 다른 곳에서 삭제되었으면 편집 상태를 정리한다.
    if st.session_state["editing_id"] and find_item(st.session_state["editing_id"]) is None:
        end_edit()


def flash(kind: str, text: str) -> None:
    """콜백에서 만든 메시지를 다음 렌더링 때 표시하도록 담아 둔다.

    kind: "warning" | "info" | "toast"
    """
    st.session_state["flash"].append((kind, text))


def render_flash() -> None:
    for kind, text in st.session_state["flash"]:
        if kind == "warning":
            st.warning(text)
        elif kind == "info":
            st.info(text)
        else:
            st.toast(text)
    st.session_state["flash"] = []


# ---------------------------------------------------------------------------
# 검증 / 유틸
# ---------------------------------------------------------------------------
def validate(name: str, quantity) -> tuple[str | None, str | None]:
    """이름과 수량을 검증한다. (정리된 이름, 오류 문구)를 반환한다."""
    name = (name or "").strip()
    if not name:
        return None, "아이템 이름을 입력해 주세요."
    if len(name) > NAME_MAX_LEN:
        return None, f"이름은 {NAME_MAX_LEN}자 이내로 입력해 주세요. (현재 {len(name)}자)"
    if (
        quantity is None
        or isinstance(quantity, bool)
        or int(quantity) != quantity
        or not QTY_MIN <= quantity <= QTY_MAX
    ):
        return None, f"수량은 {QTY_MIN}~{QTY_MAX} 사이의 정수로 입력해 주세요."
    return name, None


def find_item(item_id: str, items: list[dict] | None = None) -> dict | None:
    items = st.session_state["items"] if items is None else items
    return next((it for it in items if it["id"] == item_id), None)


def apply(fn):
    """storage.update_items 래퍼: 최신 파일 기준으로 수정·저장하고 화면 상태를 갱신한다."""
    items, result, warning = update_items(fn)
    st.session_state["items"] = items
    if warning:
        flash("warning", warning)
    return result


_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!~|<>$])")


def md_safe(text: str) -> str:
    """토스트/안내처럼 마크다운으로만 표시되는 곳에 사용자 입력을 넣을 때 쓴다.

    마크다운 특수문자는 백슬래시로, Streamlit 전용 문법(:red[...], :material/...:)은
    콜론 뒤에 폭 없는 공백을 넣어 해석되지 않게 한다.
    """
    return _MD_SPECIAL.sub(r"\\\1", text).replace(":", ":​")


# ---------------------------------------------------------------------------
# 상태 변경 함수 (모두 파일의 최신 목록을 기준으로 처리)
# ---------------------------------------------------------------------------
def add_item(name: str, quantity: int) -> bool:
    """새 아이템을 목록 맨 아래에 추가하고 저장한다. (F-01)"""
    clean, error = validate(name, quantity)
    if error:
        flash("warning", error)
        return False

    def op(items: list[dict]) -> bool:
        duplicated = any(it["name"] == clean for it in items)
        items.append(
            {
                "id": str(uuid.uuid4()),
                "name": clean,
                "quantity": int(quantity),
                "checked": False,
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        return duplicated

    if apply(op):
        flash("info", f"이미 같은 이름의 항목이 있습니다: '{md_safe(clean)}'")
    return True


def update_item(item_id: str, name: str, quantity: int) -> bool:
    """id로 아이템을 찾아 이름과 수량을 수정하고 저장한다. (F-03)"""
    clean, error = validate(name, quantity)
    if error:
        flash("warning", error)
        return False

    def op(items: list[dict]) -> bool:
        item = find_item(item_id, items)
        if item is None:
            return False
        item["name"] = clean
        item["quantity"] = int(quantity)
        return True

    if not apply(op):
        flash("warning", "이미 삭제된 항목입니다.")
    return True


def delete_item(item_id: str) -> None:
    """id로 아이템을 찾아 삭제하고 저장한다. (F-04)"""

    def op(items: list[dict]) -> dict | None:
        item = find_item(item_id, items)
        if item is not None:
            items.remove(item)
        return item

    removed = apply(op)
    if st.session_state["editing_id"] == item_id:
        end_edit()
    if removed is not None:
        flash("toast", f"'{md_safe(removed['name'])}' 항목을 삭제했습니다.")


def toggle_item(item_id: str) -> None:
    """체크 상태를 체크박스의 현재 값으로 맞추고 저장한다. (F-05)

    단순히 뒤집지 않고 사용자가 본 체크박스 값을 그대로 저장한다.
    (다른 탭에서 이미 바꾼 경우에도 사용자가 의도한 상태가 된다)
    """
    wanted = st.session_state.get(f"check_{item_id}")

    def op(items: list[dict]) -> None:
        item = find_item(item_id, items)
        if item is not None:
            item["checked"] = (not item["checked"]) if wanted is None else bool(wanted)

    apply(op)


def clear_checked() -> None:
    """체크된 아이템을 모두 삭제하고 저장한다. (F-08)"""

    def op(items: list[dict]) -> list[dict]:
        removed = [it for it in items if it["checked"]]
        items[:] = [it for it in items if not it["checked"]]
        return removed

    removed = apply(op)
    if st.session_state["editing_id"] in {it["id"] for it in removed}:
        end_edit()
    if removed:
        flash("toast", f"완료 항목 {len(removed)}개를 지웠습니다.")


# ---------------------------------------------------------------------------
# 위젯 콜백
# ---------------------------------------------------------------------------
def on_add_submit() -> None:
    # 성공했을 때만 입력칸을 비운다. (실패하면 입력값을 남겨 고칠 수 있게)
    if add_item(st.session_state["new_name"], st.session_state["new_qty"]):
        st.session_state["new_name"] = ""
        st.session_state["new_qty"] = 1
        st.session_state["page"] = 10**9  # 새 항목은 맨 아래에 있으므로 마지막 페이지로 이동 (렌더링 때 보정)


def set_page(page: int) -> None:
    st.session_state["page"] = page


def edit_keys(item_id: str) -> tuple[str, str]:
    return f"edit_name_{item_id}", f"edit_qty_{item_id}"


def on_start_edit(item_id: str) -> None:
    # 다른 항목을 편집 중이면 시작하지 않는다. (버튼도 비활성화되어 있음)
    if st.session_state["editing_id"] not in (None, item_id):
        return
    item = find_item(item_id)
    if item is None:
        return
    st.session_state["edit_draft"] = {"name": item["name"], "quantity": item["quantity"]}
    k_name, k_qty = edit_keys(item_id)
    st.session_state[k_name] = item["name"]
    st.session_state[k_qty] = item["quantity"]
    st.session_state["editing_id"] = item_id


def on_edit_change(item_id: str) -> None:
    """편집 입력값을 위젯과 별도로 보관한다.

    위젯이 화면에서 잠시 사라지면(예: 필터 전환) Streamlit이 위젯 값을 지우므로,
    다시 그릴 때 이 값으로 복원한다.
    """
    k_name, k_qty = edit_keys(item_id)
    st.session_state["edit_draft"] = {
        "name": st.session_state.get(k_name, ""),
        "quantity": st.session_state.get(k_qty),
    }


def on_save_edit(item_id: str) -> None:
    on_edit_change(item_id)
    draft = st.session_state["edit_draft"]
    if update_item(item_id, draft["name"], draft["quantity"]):
        end_edit()


def end_edit() -> None:
    st.session_state["editing_id"] = None
    st.session_state["edit_draft"] = None


# ---------------------------------------------------------------------------
# 스타일 (커스텀 CSS는 이곳 한 곳에서만 관리)
#   - Streamlit 자동 생성 클래스(st-emotion-cache-*)는 쓰지 않는다.
#   - st.container(key=...)가 만드는 .st-key-<key> 클래스와 data-testid만 사용한다.
#   - 아이템 카드 key 규칙: row_<id>(기본) / rowdone_<id>(완료) / rowedit_<id>(편집)
# ---------------------------------------------------------------------------
CSS = """
<style>
[data-testid="stMainBlockContainer"] {
    max-width: 640px;
    padding-top: 2.5rem;
    padding-left: 1rem;
    padding-right: 1rem;
}
h1 { font-size: 2rem !important; }

/* 카드 공통 */
.st-key-header_card, .st-key-add_card, [class*="st-key-row"] {
    background: #ffffff;
    box-shadow: 0 1px 3px rgba(16, 64, 32, 0.06);
}
.st-key-header_card .stat {
    font-size: 1.05rem;
    font-weight: 600;
}

/* 카드 안 가로 배치는 모바일에서도 줄바꿈하지 않는다 */
.st-key-add_card [data-testid="stHorizontalBlock"],
[class*="st-key-row"] [data-testid="stHorizontalBlock"] {
    flex-wrap: nowrap !important;
    gap: 0.5rem;
}
.st-key-add_card [data-testid="stColumn"],
[class*="st-key-row"] [data-testid="stColumn"] {
    min-width: 0 !important;
}

/* 버튼 글자가 좁은 화면에서 두 줄로 깨지지 않게 */
.st-key-add_card button p { white-space: nowrap; }

/* 아이템 이름 + 수량 배지 (한 줄: 이름은 왼쪽, 배지는 오른쪽) */
[class*="st-key-row"] .item-line {
    display: flex;
    align-items: center;
    gap: 0.75rem;
}
[class*="st-key-row"] .item-name {
    flex: 1;
    min-width: 0;
    overflow-wrap: anywhere;
    font-size: 1.05rem;
    line-height: 1.6;
}
[class*="st-key-row"] .qty {
    flex: none;
    font-size: 0.8rem;
    font-weight: 600;
    padding: 0.1rem 0.45rem;
    border-radius: 0.5rem;
    color: #15803d;
    background: #dcfce7;
}
[class*="st-key-rowdone_"] .qty {
    color: #6b7280;
    background: #e5e7eb;
}
[class*="st-key-row"] [data-testid="stMarkdownContainer"] {
    margin-bottom: 0 !important;
}

/* 완료 항목: 흐린 회색 + 취소선 */
[class*="st-key-rowdone_"] { background: #f7f9f8; }
[class*="st-key-rowdone_"] .item-name {
    color: #9aa5a0;
    text-decoration: line-through;
}

/* 편집 중인 카드 */
[class*="st-key-rowedit_"] {
    border: 2px solid #16a34a !important;
    background: #f0fbf3;
}

/* 빈 목록 안내 */
.st-key-empty_state {
    text-align: center;
    padding: 2.5rem 1rem;
    color: #6b7a72;
}
.st-key-empty_state [data-testid="stMarkdownContainer"],
.st-key-empty_state [data-testid="stMarkdownContainer"] p {
    text-align: center;
    color: #6b7a72;
}
.st-key-empty_state .icon { font-size: 3rem; line-height: 1; margin-bottom: 0.5rem; }
</style>
"""


def inject_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# 화면 영역
# ---------------------------------------------------------------------------
def render_header() -> None:
    st.title("🛒 쇼핑리스트")
    items = st.session_state["items"]
    total = len(items)
    done = sum(1 for it in items if it["checked"])
    ratio = done / total if total else 0.0
    with st.container(border=True, key="header_card"):
        # 숫자만 들어가는 고정 문구이므로 HTML 사용
        st.markdown(f'<div class="stat">전체 {total}개 · 완료 {done}개</div>', unsafe_allow_html=True)
        st.progress(ratio, text=f"{round(ratio * 100)}% 완료")


def render_add_form() -> None:
    # 수량 범위·글자 수는 위젯이 아니라 validate()에서 검사한다.
    # (위젯 제한을 쓰면 범위 밖 값이 안내 없이 무시되거나 붙여넣기가 통째로 지워짐)
    with st.container(border=True, key="add_card"):
        with st.form("add_form", border=False):
            c_name, c_qty, c_btn = st.columns([4.2, 2, 1.4], vertical_alignment="bottom")
            c_name.text_input("아이템 이름", key="new_name", placeholder="예: 우유")
            c_qty.number_input("수량", key="new_qty", step=1)
            c_btn.form_submit_button("추가", type="primary", on_click=on_add_submit, width="stretch")


def render_filter() -> str:
    return st.radio(
        "보기", FILTER_OPTIONS, key="filter", horizontal=True, label_visibility="collapsed",
        on_change=set_page, args=(0,),
    )


def render_edit_row(item: dict) -> None:
    item_id = item["id"]
    k_name, k_qty = edit_keys(item_id)
    draft = st.session_state["edit_draft"] or {"name": item["name"], "quantity": item["quantity"]}
    # 위젯 값이 지워졌으면(필터 전환 등) 보관해 둔 편집 값으로 복원한다.
    if k_name not in st.session_state:
        st.session_state[k_name] = draft["name"]
    if k_qty not in st.session_state:
        st.session_state[k_qty] = draft["quantity"]

    with st.container(border=True, key=f"rowedit_{item_id}"):
        st.text_input("이름", key=k_name, on_change=on_edit_change, args=(item_id,))
        c_qty, c_save, c_cancel = st.columns([2, 1, 1], vertical_alignment="bottom")
        c_qty.number_input("수량", key=k_qty, step=1, on_change=on_edit_change, args=(item_id,))
        c_save.button("💾", key=f"save_{item_id}", help="저장", type="primary",
                      on_click=on_save_edit, args=(item_id,), width="stretch")
        c_cancel.button("✖️", key=f"cancel_{item_id}", help="취소",
                        on_click=end_edit, width="stretch")


def render_item_row(item: dict) -> None:
    item_id = item["id"]
    if st.session_state["editing_id"] == item_id:
        render_edit_row(item)
        return

    # 체크박스를 파일의 값으로 맞춘다. (다른 탭에서 바뀐 경우 대비)
    # 성능: 값이 다를 때만 설정한다. 매번 설정하면 위젯 값을 매 실행마다 다시 보내 느려진다.
    k_check = f"check_{item_id}"
    if st.session_state.get(k_check) != item["checked"]:
        st.session_state[k_check] = item["checked"]
    editing_other = st.session_state["editing_id"] is not None

    key = f"rowdone_{item_id}" if item["checked"] else f"row_{item_id}"
    with st.container(border=True, key=key):
        c_chk, c_name, c_edit, c_del = st.columns([0.55, 6.1, 0.7, 0.7], vertical_alignment="center")
        c_chk.checkbox(
            item["name"],
            key=k_check,
            on_change=toggle_item,
            args=(item_id,),
            label_visibility="collapsed",
        )
        # 이름은 HTML 이스케이프한 일반 텍스트로 표시한다. 한 줄짜리 HTML 블록은
        # 마크다운 문법(:red[...], **, $ 등)이 해석되지 않는다.
        # 성능: 행마다 요소 수를 줄이려고 이름과 수량 배지를 한 요소에 넣는다.
        # (st.html은 호출마다 파일 경로 검사를 해서 500개에서 느림)
        # 콜론은 HTML 엔티티로 바꾼다. (:material/...: 아이콘 문법은 HTML 안에서도 해석되므로)
        safe_name = html.escape(item["name"]).replace(":", "&#58;")
        c_name.markdown(
            f'<div class="item-line"><span class="item-name">{safe_name}</span>'
            f'<span class="qty">x{item["quantity"]}</span></div>',
            unsafe_allow_html=True,
        )
        c_edit.button(
            "✏️",
            key=f"edit_{item_id}",
            help="편집 중인 항목을 먼저 저장하거나 취소하세요." if editing_other else "수정",
            type="tertiary",
            disabled=editing_other,
            on_click=on_start_edit,
            args=(item_id,),
        )
        c_del.button("🗑️", key=f"del_{item_id}", help="삭제", type="tertiary",
                     on_click=delete_item, args=(item_id,))


def render_item_list(view: str) -> None:
    items = st.session_state["items"]
    if not items:
        with st.container(key="empty_state"):
            st.markdown(
                '<div class="icon">🧺</div>\n\n쇼핑리스트가 비어 있습니다. 아이템을 추가해 보세요.',
                unsafe_allow_html=True,
            )
        return
    if view == "미완료":
        items = [it for it in items if not it["checked"]]
    elif view == "완료":
        items = [it for it in items if it["checked"]]
    if not items:
        st.caption(f"'{view}' 항목이 없습니다.")
        return
    pages = (len(items) + PAGE_SIZE - 1) // PAGE_SIZE
    page = min(max(st.session_state["page"], 0), pages - 1)
    st.session_state["page"] = page
    start = page * PAGE_SIZE
    for item in items[start:start + PAGE_SIZE]:
        render_item_row(item)
    if pages > 1:
        render_pager(page, pages, start, len(items))


def render_pager(page: int, pages: int, start: int, total: int) -> None:
    end = min(start + PAGE_SIZE, total)
    with st.container(horizontal=True, horizontal_alignment="center",
                      vertical_alignment="center", key="pager"):
        st.button("◀ 이전", key="page_prev", disabled=page == 0, on_click=set_page, args=(page - 1,))
        st.caption(f"{page + 1} / {pages} 페이지 · {start + 1}–{end} / {total}개")
        st.button("다음 ▶", key="page_next", disabled=page >= pages - 1, on_click=set_page, args=(page + 1,))


def render_footer() -> None:
    has_checked = any(it["checked"] for it in st.session_state["items"])
    with st.container(horizontal=True, horizontal_alignment="right"):
        st.button("완료 항목 지우기", on_click=clear_checked, disabled=not has_checked)


def main() -> None:
    init_state()
    inject_css()
    render_header()
    render_add_form()
    render_flash()
    view = render_filter()
    render_item_list(view)
    render_footer()


main()
