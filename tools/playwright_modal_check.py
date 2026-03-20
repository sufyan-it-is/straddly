from playwright.sync_api import sync_playwright

AUTH_TOKEN = '1|GQq5Q1JESHaawnDJ5kvW0lFevUgU4o2abzcH27y2b3b38466'
AUTH_USER_ID = 'x8gg0og8440wkgc8ow0ococs'
TARGET_URL = 'https://straddly.pro/trade'
TARGET_TEXT = 'RELIANCE'

def run():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context()
        # preload auth into localStorage
        ctx.add_init_script("window.localStorage.setItem('authToken', '%s'); window.localStorage.setItem('authUser', JSON.stringify({id: '%s'}));" % (AUTH_TOKEN, AUTH_USER_ID))
        page = ctx.new_page()
        page.goto(TARGET_URL, timeout=60000)
        try:
            page.wait_for_selector(f"text={TARGET_TEXT}", timeout=20000)
        except Exception as e:
            print('FAILED: target symbol not found on page', e)
            browser.close()
            return

        # Try clicking the symbol text
        try:
            page.click(f"text={TARGET_TEXT}", timeout=5000)
        except Exception:
            # try to click nearby BUY button if present
            try:
                # find element that contains the symbol then click a nearby BUY button
                buy_btn = page.locator(f"xpath=//*[contains(translate(text(), 'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'), '{TARGET_TEXT}')]/following::button[contains(translate(normalize-space(.), 'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'), 'BUY')][1]")
                if buy_btn.count() > 0:
                    buy_btn.first.click()
                else:
                    # fallback: click any BUY button
                    page.click("text=BUY", timeout=5000)
            except Exception as e:
                print('FAILED: could not click to open order modal', e)
                browser.close()
                return

        # Wait for required margin text to appear in modal
        try:
            page.wait_for_selector("text=Required Margin", timeout=15000)
            el = page.locator("text=Required Margin").first
            parent = el.locator('..')
            text = parent.text_content()
            print('OK: modal found —', text.strip())
        except Exception as e:
            print('FAILED: modal did not show required margin', e)
        browser.close()

if __name__ == '__main__':
    run()
