# 离线回归：用临时存储与必要的模型替身验证边界；通过测试不等于真实模型准确率。

from collections import Counter
from html.parser import HTMLParser
from suite.app import create_app
from suite.core import Config
from fastapi.testclient import TestClient

# 检查静态资源可读、元素ID唯一与label关联；不替代浏览器视觉验收。
def test_frontend_page_assets_and_labels(tmp_path):
    client = TestClient(create_app(Config(data=tmp_path)))
    class Page(HTMLParser):
        def __init__(self):
            super().__init__(); self.ids=[]; self.labels=[]; self.assets=[]
        def handle_starttag(self, tag, attrs):
            attrs=dict(attrs)
            if attrs.get('id'): self.ids.append(attrs['id'])
            if tag=='label' and attrs.get('for'): self.labels.append(attrs['for'])
            value=attrs.get('src') if tag=='script' else attrs.get('href') if tag=='link' else None
            if value and value.startswith('/static/'): self.assets.append(value)
    page=Page(); response=client.get('/'); page.feed(response.text)
    assert response.status_code==200
    assert not [key for key,count in Counter(page.ids).items() if count>1]
    assert set(page.labels)<=set(page.ids)
    assert any(path.startswith('/static/ui.js') for path in page.assets)
    for path in page.assets: assert client.get(path).status_code==200
    assert client.get('/static/icons.svg').status_code==200
