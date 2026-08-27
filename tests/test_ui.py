import sys
from playwright.sync_api import sync_playwright
import time
import os

def run_tests():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        # We need the absolute paths of our fixtures
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        fixture_dxf = os.path.join(base_dir, "tests", "fixtures", "fixture_simple.dxf")
        fixture_pdf = os.path.join(base_dir, "tests", "fixtures", "fixture_simple.pdf")
        fixture_img = os.path.join(base_dir, "tests", "floor-plan-with-furniture-blueprint-illustration.jpg")
        fixture_corrupt = os.path.join(base_dir, "tests", "fixtures", "fixture_corrupt.pdf")
        
        print("--- RUNNING E2E UI TESTS ---")
        
        print("1. Testing Successful Round-Trips (DXF, PDF, Image)...")
        for fmt, fixture in [("DXF", fixture_dxf), ("PDF", fixture_pdf), ("Image", fixture_img)]:
            print(f"   -> Uploading {fmt}...")
            page.goto("http://127.0.0.1:5000/")
            page.locator("input#file").set_input_files(fixture)
            page.locator("button#submitBtn").click()
            
            # Wait for model viewer or error panel
            try:
                page.wait_for_selector("model-viewer", state="visible", timeout=30000)
                print(f"      {fmt} rendering successfully.")
            except:
                if page.is_visible("#errorPanel"):
                    print("      FAILED! Error panel says:", page.locator("#errorPanel").text_content())
                raise
            
        print("\n2. Uploading Corrupted PDF (Checking error panel text)...")
        page.goto("http://127.0.0.1:5000/")
        page.locator("input#file").set_input_files(fixture_corrupt)
        page.locator("button#submitBtn").click()
        
        page.wait_for_selector("#errorPanel", state="visible", timeout=60000)
        error_text = page.locator("#errorPanel").text_content()
        print("UI ERROR PANEL TEXT:")
        print(" -", error_text.strip())
        
        assert "Parsing stage failed" in error_text, "ParserError not surfaced properly!"
        print(" -> Error State Test PASSED")
        
        browser.close()
        print("\nALL UI TESTS PASSED!")

if __name__ == "__main__":
    run_tests()
