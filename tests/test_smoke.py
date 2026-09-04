"""اختبار Smoke بسيط للتأكد أن التطبيق يبدأ."""


def test_login_page_loads(client):
    response = client.get("/auth/login")
    assert response.status_code == 200
    assert "دكتور بورسلين".encode("utf-8") in response.data


def test_index_redirects_to_login(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (301, 302)
    assert "/auth/login" in response.headers["Location"]


def test_dashboard_requires_auth(client):
    response = client.get("/dashboard", follow_redirects=False)
    assert response.status_code in (301, 302)
    assert "/auth/login" in response.headers["Location"]
