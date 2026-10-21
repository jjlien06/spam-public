from playwright.sync_api import sync_playwright
from job_agent.custom_controls import fill_comboboxes
from job_agent.profile import Profile


def test_selected_multiselect_preference_is_not_searched_again():
    with sync_playwright() as p:
        browser=p.chromium.launch()
        page=browser.new_page()
        page.set_content('''<label for="source">How did you hear about us?</label>
          <div class="select__control"><span class="select__multi-value__label">Company Website</span>
          <span class="select__multi-value__label">Other</span><input id="source" role="combobox"></div>
          <script>window.clicks=0;document.querySelector('input').onclick=()=>window.clicks++;</script>''')
        profile=Profile({'answers':{'How did you hear about us?':['LinkedIn','Company Website']}})
        assert fill_comboboxes(page.main_frame,profile,lambda:False,lambda _:None)==(0,[])
        assert page.evaluate('window.clicks')==0
        browser.close()


def test_late_selected_preference_stops_workday_fallback_search(monkeypatch):
    from job_agent import repeated_sections as module
    class Field:
        calls=0
        def evaluate(self, expression):
            self.calls+=1
            return ['LinkedIn'] if self.calls>=4 else []
    class Page:
        def wait_for_timeout(self, ms): pass
    searches=[]
    monkeypatch.setattr(module,'fill_search',lambda page,field,choice: searches.append(choice) or False)
    monkeypatch.setattr(module,'close_prompts',lambda page: None)
    assert module.search_any(Page(),Field(),['LinkedIn','Job Board'])
    assert searches==['LinkedIn']
