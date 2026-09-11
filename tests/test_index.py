from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_index_serves_the_query_page():
    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert '<textarea id="sql"' in response.text
    assert 'id="run"' in response.text
