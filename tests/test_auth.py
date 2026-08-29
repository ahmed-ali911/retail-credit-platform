from tests.conftest import TEST_PASSWORD


def test_login_succeeds_with_correct_credentials(client_as):
    anon = client_as(None)
    r = anon.post("/auth/login", json={"username": "u_admin", "password": TEST_PASSWORD})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["expires_in"] > 0


def test_login_fails_with_wrong_password(client_as):
    r = client_as(None).post(
        "/auth/login", json={"username": "u_admin", "password": "wrong"}
    )
    assert r.status_code == 401


def test_login_fails_for_unknown_user(client_as):
    r = client_as(None).post(
        "/auth/login", json={"username": "nobody", "password": TEST_PASSWORD}
    )
    assert r.status_code == 401


def test_me_returns_current_user(client_as):
    r = client_as("credit_officer").get("/auth/me")
    assert r.status_code == 200
    assert r.json()["role"] == "credit_officer"
    assert r.json()["username"] == "u_credit_officer"


def test_me_requires_a_token(client_as):
    assert client_as(None).get("/auth/me").status_code == 401


def test_register_is_admin_only(client_as):
    body = {"username": "new_sales", "password": "pw123456", "role": "sales_employee"}

    assert client_as("credit_manager").post("/auth/register", json=body).status_code == 403
    assert client_as(None).post("/auth/register", json=body).status_code == 401

    r = client_as("admin").post("/auth/register", json=body)
    assert r.status_code == 201, r.text
    assert r.json()["role"] == "sales_employee"

    # the new user can log in
    login = client_as(None).post(
        "/auth/login", json={"username": "new_sales", "password": "pw123456"}
    )
    assert login.status_code == 200


def test_register_rejects_duplicate_username(client_as):
    body = {"username": "u_admin", "password": "pw123456", "role": "customer"}
    assert client_as("admin").post("/auth/register", json=body).status_code == 409


def test_bad_token_is_401(client_as):
    c = client_as(None)
    c.headers["Authorization"] = "Bearer not-a-real-token"
    assert c.get("/auth/me").status_code == 401
