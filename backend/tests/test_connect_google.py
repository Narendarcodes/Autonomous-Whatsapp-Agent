"""Tests for B3: "Connect Google" dashboard flow (TDD).

Tracer bullets:
1. Tenant-aware status endpoint returns connected=false when no token
2. Returns connected=true + account info when the tenant has a token
3. Cross-tenant isolation: tenant B never sees tenant A's token status
4. Dashboard page offers the one-click Connect Google card
"""
import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.main import app
from app.core.security import hash_password, encrypt_token
from app.core.auth import create_session, SESSION_COOKIE
from app.models.models import Tenant, DashboardUser, User, CustomerGoogleToken
from app.db.database import AsyncSessionLocal


async def _seed_tenant_with_owner(slug: str, email: str, password: str):
    """Tenant + DashboardUser (+ matching WhatsApp owner User row)."""
    async with AsyncSessionLocal() as db:
        t = Tenant(name=slug.title(), slug=slug, is_active=True)
        db.add(t)
        await db.flush()
        d = DashboardUser(
            tenant_id=t.id, email=email,
            password_hash=hash_password(password), is_owner=True,
        )
        u = User(wa_phone=f"9{abs(hash(slug)) % 10**9:09d}", tenant_id=t.id,
                 is_owner=True, has_permission=True)
        db.add_all([d, u])
        await db.commit()
        await db.refresh(d)
        return t.id, d.id


def _select_all(model):
    from sqlalchemy import select
    return select(model)


async def _client_for(tenant_id: int, dash_user_id: int) -> AsyncClient:
    sid = await create_session(tenant_id, dash_user_id)
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={SESSION_COOKIE: sid},
    )


@pytest.mark.asyncio
async def test_status_connected_false_when_no_token(test_engine):
    """Cycle 1: authenticated tenant without a Google token → {connected: false}."""
    tenant_id, dash_id = await _seed_tenant_with_owner("b3a", "a@b3.test", "pw123456")

    async with await _client_for(tenant_id, dash_id) as ac:
        resp = await ac.get("/api/google-connection-status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["connected"] is False


@pytest.mark.asyncio
async def test_status_connected_true_when_tenant_has_token(test_engine):
    """Cycle 2: token row exists for the tenant → connected=true + email."""
    tenant_id, dash_id = await _seed_tenant_with_owner("b3b", "b@b3.test", "pw123456")

    async with AsyncSessionLocal() as db:
        db.add(CustomerGoogleToken(
            tenant_id=tenant_id,
            user_wa_phone="9111222333",
            access_token_enc=encrypt_token("at"),
            email="owner@gmail.com",
            scopes="https://www.googleapis.com/auth/calendar",
        ))
        await db.commit()

    async with await _client_for(tenant_id, dash_id) as ac:
        resp = await ac.get("/api/google-connection-status")
        data = resp.json()
        assert resp.status_code == 200
        assert data["connected"] is True
        assert data["email"] == "owner@gmail.com"
        assert any("calendar" in s for s in data["scopes"])


@pytest.mark.asyncio
async def test_status_isolated_across_tenants(test_engine):
    """Cycle 2b: tenant B must NOT see tenant A's token (isolation)."""
    id_a, dash_a = await _seed_tenant_with_owner("b3c", "c@b3.test", "pw123456")
    id_b, dash_b = await _seed_tenant_with_owner("b3d", "d@b3.test", "pw123456")

    # Only tenant A has a token
    async with AsyncSessionLocal() as db:
        db.add(CustomerGoogleToken(
            tenant_id=id_a,
            user_wa_phone="9444555666",
            access_token_enc=encrypt_token("at-a"),
            email="a@gmail.com",
        ))
        await db.commit()

    async with await _client_for(id_b, dash_b) as ac:
        data = (await ac.get("/api/google-connection-status")).json()
        assert data["connected"] is False  # B sees nothing of A's

    async with await _client_for(id_a, dash_a) as ac:
        data = (await ac.get("/api/google-connection-status")).json()
        assert data["connected"] is True   # A sees its own


@pytest.mark.asyncio
async def test_status_requires_auth(test_engine):
    """No session → 401, not a status leak."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/google-connection-status")
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_dashboard_shows_connect_google_card(test_engine):
    """Cycle 3: /dashboard HTML contains the one-click Connect Google card."""
    tenant_id, dash_id = await _seed_tenant_with_owner("b3e", "e@b3.test", "pw123456")

    async with await _client_for(tenant_id, dash_id) as ac:
        resp = await ac.get("/dashboard")
        assert resp.status_code == 200
        html = resp.text
        assert "Connect Google" in html
        assert "/oauth/authorize" in html          # the one-click entry point
        assert 'id="google-connection-card"' in html


@pytest.mark.asyncio
async def test_dashboard_requires_auth(test_engine):
    """Unauthenticated /dashboard → redirect to /login."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/dashboard", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/login"


@pytest.mark.asyncio
async def test_dashboard_wires_live_status_refresh(test_engine):
    """Cycle 4: dashboard JS must call the tenant status endpoint and flip
    the card to Connected state (chip + button swap)."""
    tenant_id, dash_id = await _seed_tenant_with_owner("b3f", "f@b3.test", "pw123456")

    async with await _client_for(tenant_id, dash_id) as ac:
        html = (await ac.get("/dashboard")).text
        # The page's script must consult the tenant-aware endpoint...
        assert "/api/google-connection-status" in html.replace("'", "'").replace('"', '')
        # ...and have both UI states ready: connected chip + connected class hook
        assert "google-status-chip" in html
        assert "google-connected" in html


def test_dashboard_script_tags_balanced(test_engine):
    """Regression (found by impeccable critique): an unbalanced <script> tag
    nests subsequent markup inside an open JS block → SyntaxError kills ALL
    dashboard interactivity. Every <script> must have its </script>."""
    import re
    from pathlib import Path

    tpl = Path(__file__).resolve().parents[1] / "app" / "templates" / "dashboard.html"
    html = tpl.read_text(encoding="utf-8")

    opens = len(re.findall(r"<script\b", html))
    closes = html.count("</script>")
    assert opens == closes, (
        f"Unbalanced script tags: {opens} <script> vs {closes} </script> — "
        f"an unclosed block turns the rest of the page into broken JS"
    )

    # The LAST script-related tag in the file must be a closer, not an opener
    last_tag = max(
        ((m.start(), m.group(0)) for m in re.finditer(r"</?script\b", html)),
        default=(None, ""),
    )[1]
    assert last_tag.startswith("</"), f"File ends inside an open script block (last tag: {last_tag!r})"


def test_dashboard_no_duplicate_static_ids(test_engine):
    """F1 (impeccable finding): duplicate ids make getElementById resolve to the
    first occurrence only — silently wrong element for connector forms.

    Only checks STATIC markup (lines outside <script> blocks); JS template
    literals legitimately reuse an id because one modal instance renders at a time.
    """
    import re
    from collections import Counter
    from pathlib import Path

    tpl = Path(__file__).resolve().parents[1] / "app" / "templates" / "dashboard.html"
    html = tpl.read_text(encoding="utf-8")

    # Strip <script>...</script> blocks (template-literal HTML is rendered one-at-a-time)
    static_html = re.sub(r"<script>.*?</script>", "", html, flags=re.S)

    ids = re.findall(r'id="([^"]+)"', static_html)
    dupes = {i: c for i, c in Counter(ids).items() if c > 1}
    assert not dupes, f"Duplicate static ids found: {dupes}"


def _static_dom() -> str:
    """Template with <script> blocks stripped (same rationale as F1)."""
    import re
    from pathlib import Path

    tpl = Path(__file__).resolve().parents[1] / "app" / "templates" / "dashboard.html"
    html = tpl.read_text(encoding="utf-8")
    return re.sub(r"<script>.*?</script>", "", html, flags=re.S)


def test_dashboard_icon_only_buttons_have_accessible_names():
    """F2a (impeccable): icon-only buttons must carry aria-label so screen
    readers announce intent, not 'button'."""
    import re

    dom = _static_dom()
    failures = []
    # Buttons whose visible content is only a material-symbols icon span
    for m in re.finditer(r"<button\b([^>]*)>(.*?)</button>", dom, re.S):
        attrs, inner = m.group(1), m.group(2)
        text = re.sub(r"<[^>]+>", "", inner).strip()
        if text:
            continue  # has visible text
        if "material-symbols-outlined" in inner or "qr" in inner.lower():
            has_label = re.search(r'aria-label="[^"]+"', attrs) or re.search(r'aria-labelledby="[^"]+"', attrs)
            if not has_label:
                line = dom[: m.start()].count("\n") + 1
                failures.append(f"L{line}: {inner.strip()[:60]}")
    assert not failures, f"Icon-only buttons missing aria-label:\n" + "\n".join(failures)


def test_dashboard_static_inputs_have_labels():
    """F2b (impeccable): every static <input>/<select> needs a label via
    for=, wrapping label, or aria-label."""
    import re

    dom = _static_dom()
    failures = []
    label_fors = set(re.findall(r'<label[^>]*\bfor="([^"]+)"', dom))
    for m in re.finditer(r"<(input|select)\b([^>]*)/?>", dom):
        tag, attrs = m.group(1), m.group(2)
        idm = re.search(r'id="([^"]+)"', attrs)
        if (
            (not idm or idm.group(1) not in label_fors)
            and "aria-label" not in attrs
            and "aria-labelledby" not in attrs
            and 'type="hidden"' not in attrs
        ):
            line = dom[: m.start()].count("\n") + 1
            failures.append(f"L{line}: <{tag} {attrs.strip()[:70]}")
    assert not failures, f"Inputs/selects without labels:\n" + "\n".join(failures)


def test_dashboard_clickable_divs_are_interactive():
    """F2c (impeccable): elements with onclick that aren't button/a/input need
    role="button" + tabindex="0" to be keyboard-reachable."""
    import re

    dom = _static_dom()
    failures = []
    for m in re.finditer(r'<(div|span|h\d|p)\b([^>]+)>', dom):
        tag, attrs = m.group(1), m.group(2)
        if "onclick" not in attrs:
            continue
        if 'role="button"' in attrs or "tabindex" in attrs:
            continue
        line = dom[: m.start()].count("\n") + 1
        failures.append(f"L{line}: <{tag} ...onclick")
    assert not failures, f"Clickable non-interactive elements (add role=button + tabindex=0):\n" + "\n".join(failures)
