"""新版多视图界面端到端截图：视图切换 + 上传 + 核算"""
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "outputs"
OUT.mkdir(exist_ok=True)
URL = "http://127.0.0.1:8765/"


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
        page.goto(URL, wait_until="networkidle")
        page.wait_for_timeout(900)
        page.screenshot(path=str(OUT / "v2_1_工作台.png"))
        print("已截图：工作台")

        # 视图切换：政策参数库 / 使用说明
        page.click('.nav a[data-view="policy"]')
        page.wait_for_timeout(500)
        page.screenshot(path=str(OUT / "v2_2_政策参数库.png"))
        print("已截图：政策参数库")
        page.click('.nav a[data-view="help"]')
        page.wait_for_timeout(400)
        page.screenshot(path=str(OUT / "v2_3_使用说明.png"))
        print("已截图：使用说明")

        # 空状态：列映射确认
        page.click('.nav a[data-view="mapping"]')
        page.wait_for_timeout(400)
        page.screenshot(path=str(OUT / "v2_4_列映射空状态.png"))
        print("已截图：列映射空状态")

        # 上传后应自动跳到列映射视图
        page.set_input_files("#file", str(ROOT / "samples" / "sample_long.xlsx"))
        page.wait_for_selector("#mapContent:not(.hide)", timeout=30000)
        page.wait_for_timeout(800)
        page.screenshot(path=str(OUT / "v2_5_列映射.png"))
        print("已截图：列映射")

        # 核算后应自动跳到闪电校验视图
        page.select_option("#city", "上海")
        page.fill("#year", "2026")
        page.fill("#client", "样例客户A")
        page.click("#btnRun")
        page.wait_for_selector("#chkContent:not(.hide)", timeout=60000)
        page.wait_for_timeout(1200)
        page.screenshot(path=str(OUT / "v2_6_闪电校验.png"))
        print("已截图：闪电校验")

        # 切到核算结果视图
        page.click('.nav a[data-view="result"]')
        page.wait_for_timeout(500)
        page.screenshot(path=str(OUT / "v2_7_核算结果.png"))
        print("已截图：核算结果")

        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
