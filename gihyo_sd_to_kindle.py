# -*- coding: utf-8 -*-
"""
Software Design（技術評論社）最新号EPUBを取得→Kindle送信（uv想定・ローカル用, デバッグ出力対応）
マイページ配下からSD誌の該当号を見つけてEPUB/ZIPをダウンロードする版。

機能:
- Playwrightで gihyo.jp にログイン（トップ→ログイン導線クリック。クッキーバナー閉じ対応）
- マイページ配下（複数タブ/URLを総当り・ページング対応）から「Software Design」を探索
- 号詳細ページで EPUB直リンク or ZIP内EPUB or ボタン押下ダウンロードを検出してDL
- SMTPで Send-to-Kindle へ送信
- 重複送信防止（work/last_sent.txt）
- storage.json があればクッキー再利用でログイン省略
- DEBUG=1 で各段階の HTML / スクショ を WORKDIR 配下に保存

使い方（一例）:
  uv sync
  uv run playwright install chromium
  # .env を同ディレクトリに配置（下の環境変数を設定）
  uv run python gihyo_sd_to_kindle.py

.env:
  GIHYO_EMAIL, GIHYO_PASSWORD
  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SENDER_EMAIL, KINDLE_EMAIL
  WORKDIR=./work（任意）
  DEBUG=1（任意：デバッグ出力ON）
"""
import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from typing import Tuple
from datetime import datetime

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, Page, BrowserContext, Playwright

# ==== 設定 ====
ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

GIHYO_EMAIL = os.environ.get("GIHYO_EMAIL", "")
GIHYO_PASSWORD = os.environ.get("GIHYO_PASSWORD", "")

SMTP_HOST = os.environ["SMTP_HOST"]
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ["SMTP_USER"]
SMTP_PASS = os.environ["SMTP_PASS"]
SENDER_EMAIL = os.environ["SENDER_EMAIL"]
KINDLE_EMAIL = os.environ["KINDLE_EMAIL"]

WORKDIR = Path(os.environ.get("WORKDIR", "./work")).resolve()
WORKDIR.mkdir(parents=True, exist_ok=True)
LAST_SENT_FILE = WORKDIR / "last_sent.txt"
STORAGE_STATE = ROOT / "storage.json"

TARGET_KEYWORD = "Software Design"
DEBUG = os.environ.get("DEBUG", "0") == "1"


# ==== 小物ユーティリティ（デバッグ用ダンプ） ====
def _stamp(name: str) -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = name.replace("/", "_").replace(":", "_")
    return f"{ts}_{safe}"

def dump(page: Page, name: str):
    if not DEBUG:
        return
    fn = _stamp(name)
    # HTML
    (WORKDIR / f"{fn}.html").write_text(page.content(), encoding="utf-8")
    # スクショ
    try:
        page.screenshot(path=str(WORKDIR / f"{fn}.png"), full_page=True)
    except Exception:
        pass


# ==== 送受信ユーティリティ ====
def already_sent(issue_tag: str) -> bool:
    return LAST_SENT_FILE.exists() and LAST_SENT_FILE.read_text().strip() == issue_tag

def mark_sent(issue_tag: str) -> None:
    LAST_SENT_FILE.write_text(issue_tag)

def watch_responses(page: Page) -> None:
    if not DEBUG:
        return
    def _on_resp(resp):
        if "gihyo.jp" in resp.url and resp.request.resource_type == "document":
            print(f"[HTTP {resp.status}] {resp.url}")
    page.on("response", _on_resp)


# ==== Playwright起動 ====
def new_context(pw: Playwright, use_storage_if_exists: bool = True) -> Tuple[BrowserContext, Page]:
    browser = pw.chromium.launch(
        headless=True,  # ヘッドレスモードで実行
        args=["--disable-blink-features=AutomationControlled"],
    )
    ctx_kwargs = dict(
        accept_downloads=True,
        locale="ja-JP",
        timezone_id="Asia/Tokyo",
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/129.0.0.0 Safari/537.36"
        ),
    )
    if use_storage_if_exists and STORAGE_STATE.exists():
        ctx_kwargs["storage_state"] = str(STORAGE_STATE)

    ctx = browser.new_context(**ctx_kwargs)
    ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    page = ctx.new_page()
    watch_responses(page)
    return ctx, page


# ==== ログイン（テキスト依存なし / iframe対応） ====
def _find_login_scope(page: Page) -> Page:
    if page.locator("input[type='password'], input[name='password'], input[autocomplete='current-password']").count() > 0:
        return page
    for fr in page.frames:
        if fr.locator("input[type='password'], input[name='password'], input[autocomplete='current-password']").count() > 0:
            return fr
    return page

def login_gihyo(page: Page) -> None:
    page.goto("https://gihyo.jp/dp", wait_until="load")

    # クッキーバナー閉じ
    try:
        cookie_btn = page.locator(
            "button:has-text('確認して閉じる'), "
            "button:has-text('同意して閉じる'), "
            "button:has-text('同意する'), "
            "button[aria-label*='同意'], button[aria-label*='閉じ']"
        )
        if cookie_btn.first.is_visible():
            cookie_btn.first.click()
            page.wait_for_timeout(300)
    except Exception:
        pass

    # ログイン導線クリック
    login_link = page.locator(
        "a:has-text('ログイン'), a:has-text('サインイン'), a[href*='signin'], a[href*='login']"
    )
    login_link.first.click()
    page.wait_for_timeout(600)

    # 入力欄を待つ
    page.wait_for_selector(
        "input[type='email'], input[name='email'], input[autocomplete='username'], "
        "input[type='password'], input[name='password'], input[autocomplete='current-password']",
        timeout=15000
    )
    scope = _find_login_scope(page)

    email_input = scope.locator("input[type='email'], input[name='email'], input[autocomplete='username']")
    pw_input    = scope.locator("input[type='password'], input[name='password'], input[autocomplete='current-password']")

    email_input.first.fill(GIHYO_EMAIL)
    pw_input.first.fill(GIHYO_PASSWORD)

    # submit ボタン → 無ければEnter
    submit = scope.locator(
        "button[type='submit']:has-text('ログイン'), "
        "input[type='submit'][value='ログイン'], "
        "button:has-text('ログイン'), button:has-text('サインイン')"
    )
    clicked = False
    try:
        if submit.first.is_visible():
            submit.first.click()
            clicked = True
    except Exception:
        pass
    if not clicked:
        pw_input.first.press("Enter")

    # 成否判定（遷移ではなくUI確認）
    page.wait_for_timeout(1800)
    if page.locator("text=マイページ").count() > 0:
        print("✅ Login success")
        return
    if page.locator("text=メールアドレスまたはパスワード").count() > 0:
        raise RuntimeError("❌ ログイン失敗：メールアドレスまたはパスワードを確認してください。")

    page.wait_for_timeout(1500)
    if page.locator("text=マイページ").count() > 0:
        print("✅ Login success (delayed)")
        return
    print("⚠️ ログイン確認できず（UI変更/要手動ログインの可能性）")


# ==== マイページからSDの最新号ダウンロード手段を見つける ====
def find_latest_sd_epub_url(page: Page) -> Tuple[str, str]:
    """
    マイページの購入済み電子書籍リストからSoftware Designの最新号を探し、
    その電子書籍詳細ページで「EPUB/ZIP」DL手段を検出する。
    戻り値: (download_hint, issue_tag)
      - download_hint は URL もしくは "__CLICK_SELECTOR__::<selector>" の形式
    """
    # まず「マイページ」リンクをクリックして遷移する
    try:
        mypage_link = page.locator("a:has-text('マイページ'), a[href*='/my']")
        if mypage_link.count() > 0:
            mypage_link.first.click()
            page.wait_for_timeout(2000)
            print(f"✅ Navigated to mypage: {page.url}")
    except Exception as e:
        print(f"⚠️ Could not click mypage link: {e}")

    # マイページ候補URLリスト
    my_candidates = [
        page.url,  # 現在のページ(マイページリンククリック後)
        "https://gihyo.jp/dp",  # DPトップ
        "https://gihyo.jp/dp/my",
    ]

    MAX_PAGES = 10
    KEY = "software design"

    def normalize(s: str) -> str:
        import unicodedata
        s = unicodedata.normalize("NFKC", s).lower()
        s = re.sub(r"\s+", " ", s)
        return s

    found_items = []  # [(detail_url, title), ...]

    for base in my_candidates:
        # まずはページネーションなしでアクセス
        try:
            page.goto(base, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(2000)  # JSレンダリングを待つ
            # 電子書籍リストの要素を待つ
            try:
                page.wait_for_selector(
                    "a[href*='/dp/ebook/'], li[id^='978-'], .list-book li",
                    timeout=8000
                )
            except Exception:
                pass

            if DEBUG: dump(page, f"my_base")

            html = page.content()
            soup = BeautifulSoup(html, "lxml")

            # マイページの電子書籍リストから Software Design を探す
            # リンクだけでなく、書籍タイトルを含む要素全体を見る
            for elem in soup.select("li[id^='978-'], .list-book li"):
                # この要素内のリンクとテキストを取得
                link = elem.find("a", href=re.compile(r"/dp/ebook/"))
                if not link:
                    continue

                href = link.get("href", "")
                # タイトルを取得 - .title クラスまたはリンク全体のテキスト
                title_elem = elem.find(class_="title") or link
                text = title_elem.get_text(" ", strip=True) if title_elem else ""

                if not href or not text:
                    continue

                ntext = normalize(text)
                if KEY in ntext:
                    detail = href if href.startswith("http") else ("https://gihyo.jp" + href)
                    found_items.append((detail, text))
                    print(f"📚 Found: {text[:50]}... -> {detail}")

            if found_items:
                break
        except Exception as e:
            print(f"⚠️ Failed to load {base}: {e}")
            continue

    if not found_items:
        dump(page, "my_no_sd_found")
        raise RuntimeError("マイページ配下で Software Design が見つかりません。購入済み電子書籍を確認してください。")

    # "YYYY年MM月号" を抽出して降順。見つからなければ 0 点で末尾。
    def score_by_issue(s: str) -> tuple:
        m = re.search(r"(20\d{2})\s*年\s*(1?\d)\s*月", s)
        if m:
            return (int(m.group(1)), int(m.group(2)))
        return (0, 0)

    found_items.sort(key=lambda it: score_by_issue(it[1]), reverse=True)
    detail_url, issue_tag = found_items[0]
    print(f"📖 Latest issue: {issue_tag}")

    # 電子書籍の詳細ページへ遷移
    page.goto(detail_url, wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    if DEBUG: dump(page, "sd_detail")

    # EPUB/ZIPダウンロードリンク・ボタンを探す
    # 1. 直リンク(.epub / .zip)
    for a in page.locator("a[href]").all():
        try:
            href = a.get_attribute("href") or ""
        except Exception:
            continue
        if not href:
            continue
        low = href.lower()
        if low.endswith(".epub") or "format=epub" in low:
            full_url = href if href.startswith("http") else ("https://gihyo.jp" + href)
            print(f"✅ Found EPUB link: {full_url}")
            return (full_url, issue_tag)
        if low.endswith(".zip") and "epub" in low:
            full_url = href if href.startswith("http") else ("https://gihyo.jp" + href)
            print(f"✅ Found ZIP link: {full_url}")
            return (full_url, issue_tag)

    # 2. マイページで書籍をクリックしてモーダルからダウンロード
    # マイページへ戻る
    try:
        print("✅ Trying mypage modal download approach...")
        mypage_link = page.locator("a:has-text('マイページ'), a[href='/dp/my-page']")
        if mypage_link.count() > 0:
            mypage_link.first.click()
            page.wait_for_timeout(2000)
            print(f"✅ Back to mypage: {page.url}")

            # 書籍アイテムを探してクリック
            book_items = page.locator("li[id^='978-']")
            print(f"📚 Found {book_items.count()} book items")

            for i in range(book_items.count()):
                item = book_items.nth(i)
                text = item.text_content() or ""
                if KEY in normalize(text):
                    print(f"✅ Found SD book item, clicking to open modal...")
                    item.click()
                    page.wait_for_timeout(3000)  # モーダルとダウンロードボタンが有効になるまで待つ

                    # モーダル内でEPUB/PDFダウンロードリンクを探す
                    # EPUBを優先的に探す
                    epub_link = None
                    pdf_link = None

                    all_links = page.locator("a[href]").all()
                    print(f"🔍 Checking {len(all_links)} links in modal...")

                    for i, a in enumerate(all_links):
                        try:
                            href = a.get_attribute("href") or ""
                            text = (a.text_content() or "").strip()

                            # EPUBとPDFのリンクを探す
                            if ".epub" in href.lower() or ".pdf" in href.lower():
                                print(f"  - Link {i}: {text[:60]} -> {href[:80]}")

                            # EPUBリンクを探す (.epub拡張子があればOK)
                            if ".epub" in href.lower() and epub_link is None:
                                epub_link = href if href.startswith("http") else ("https://gihyo.jp" + href)
                                print(f"✅ Found EPUB download link: {epub_link}")

                            # PDFリンクもバックアップとして保存
                            elif ".pdf" in href.lower() and pdf_link is None:
                                pdf_link = href if href.startswith("http") else ("https://gihyo.jp" + href)
                                print(f"📄 Found PDF download link: {pdf_link}")
                        except Exception as e:
                            print(f"  ⚠️ Error processing link {i}: {e}")
                            continue

                    # EPUBが見つかればそれを返す、なければPDF
                    if epub_link:
                        return (epub_link, issue_tag)
                    elif pdf_link:
                        print("⚠️ EPUB not found, using PDF instead")
                        return (pdf_link, issue_tag)
                    break
    except Exception as e:
        print(f"⚠️ Error in mypage modal download: {e}")

    dump(page, "sd_detail_no_epub")
    raise RuntimeError(f"{issue_tag} のEPUB/ZIPダウンロード手段が見つかりませんでした。")


# ==== DL（URL直/ボタン押下両対応） & ZIP→EPUB抽出 ====
def download_asset(page: Page, download_hint: str, dest_dir: Path) -> Path:
    """
    download_hint:
      - URL の場合 → window.location.href で遷移させて expect_download
      - "__CLICK_SELECTOR__::<selector>" の場合 → セレクタをクリックして expect_download
    ZIPならEPUBを取り出して返す
    """
    import zipfile
    dest_dir.mkdir(parents=True, exist_ok=True)

    if download_hint.startswith("__CLICK_SELECTOR__::"):
        selector = download_hint.split("::", 1)[1]

        # オーバーレイやモーダルを閉じる試み
        try:
            close_selectors = [
                "button.close",
                "[aria-label*='閉じる']",
                "[class*='close']",
                ".modal-close",
            ]
            for close_sel in close_selectors:
                closer = page.locator(close_sel)
                if closer.count() > 0 and closer.first.is_visible():
                    closer.first.click()
                    page.wait_for_timeout(500)
                    break
        except Exception:
            pass

        # force: Trueでクリックを試みる
        with page.expect_download() as dl_info:
            try:
                page.locator(selector).first.click(force=True)
            except Exception:
                # それでも失敗する場合はJavaScriptで直接クリック
                page.locator(selector).first.evaluate("el => el.click()")
        d = dl_info.value
    else:
        url = download_hint
        with page.expect_download() as dl_info:
            page.evaluate("(u)=>window.location.href=u", url)
        d = dl_info.value

    out_path = dest_dir / d.suggested_filename
    d.save_as(str(out_path))

    # ZIPならEPUB抽出
    if out_path.suffix.lower() == ".zip":
        with zipfile.ZipFile(out_path, "r") as zf:
            epubs = [m for m in zf.namelist() if m.lower().endswith(".epub")]
            if not epubs:
                raise RuntimeError("ZIP内にEPUBが見つかりませんでした")
            zf.extract(epubs[0], dest_dir)
            extracted = dest_dir / epubs[0]
            final = dest_dir / Path(epubs[0]).name
            if extracted != final:
                extracted.rename(final)
        try:
            out_path.unlink()
        except Exception:
            pass
        return final

    return out_path


# ==== Kindle送信 ====
def send_to_kindle(epub_path: Path) -> None:
    # ファイルサイズチェック (Gmailの制限は25MB)
    file_size_mb = epub_path.stat().st_size / (1024 * 1024)
    if file_size_mb > 25:
        raise RuntimeError(f"❌ ファイルサイズが大きすぎます ({file_size_mb:.1f}MB > 25MB制限)")

    # EPUBファイルのみ送信
    if not epub_path.suffix.lower() == ".epub":
        raise RuntimeError(f"❌ EPUBファイルのみ送信可能です。実際: {epub_path.suffix}")

    msg = EmailMessage()
    msg["Subject"] = ""  # EPUBは件名Convert不要
    msg["From"] = SENDER_EMAIL
    msg["To"] = KINDLE_EMAIL
    msg.set_content(f"Automated delivery of Software Design EPUB ({file_size_mb:.1f}MB).")
    with open(epub_path, "rb") as f:
        data = f.read()
    # Kindleは .epub のMIMEを application/epub+zip 扱い
    msg.add_attachment(data, maintype="application", subtype="epub+zip", filename=epub_path.name)

    print(f"📧 Sending EPUB to Kindle ({file_size_mb:.1f}MB)...")
    ctx = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.starttls(context=ctx)
        s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)
    print("✅ Successfully sent to Kindle")


# ==== メイン ====
def main() -> None:
    with sync_playwright() as p:
        ctx, page = new_context(pw=p, use_storage_if_exists=True)

        # storage.json が無い時だけログイン実施
        if not STORAGE_STATE.exists():
            for attempt in range(2):
                try:
                    login_gihyo(page)
                    break
                except Exception as e:
                    if attempt == 0:
                        page.wait_for_timeout(1200)
                        continue
                    raise
            try:
                ctx.storage_state(path=str(STORAGE_STATE))
            except Exception:
                pass

        download_hint, issue_tag = find_latest_sd_epub_url(page)
        print("Detected:", issue_tag, download_hint)

        if already_sent(issue_tag):
            print("Already sent:", issue_tag)
            ctx.close(); ctx.browser.close()
            return

        epub_path = download_asset(page, download_hint, WORKDIR)
        print("Downloaded:", epub_path)

        send_to_kindle(epub_path)
        mark_sent(issue_tag)
        print("Sent to Kindle:", issue_tag)

        ctx.close(); ctx.browser.close()


if __name__ == "__main__":
    main()
