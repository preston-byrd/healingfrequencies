# Admin plan-prices endpoint regression tests
# Verifies PUT /api/admin/plan-prices persists changes and is reflected in
# both GET /api/admin/plan-prices (admin) and GET /api/plan/config (public).
import os
import pytest
import requests

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://frequency-healer-31.preview.emergentagent.com').rstrip('/')

ADMIN_EMAIL = 'admin@example.com'
ADMIN_PASSWORD = 'admin123'


@pytest.fixture(scope='module')
def admin_session():
    s = requests.Session()
    s.headers.update({'Content-Type': 'application/json'})
    r = s.post(f"{BASE_URL}/api/auth/login", json={'email': ADMIN_EMAIL, 'password': ADMIN_PASSWORD})
    assert r.status_code == 200, f"Admin login failed: {r.status_code} {r.text}"
    token = r.json().get('access_token') or r.json().get('token')
    if token:
        s.headers.update({'Authorization': f'Bearer {token}'})
    yield s


@pytest.fixture(scope='module')
def original_prices(admin_session):
    """Read existing prices to restore at end of module."""
    r = admin_session.get(f"{BASE_URL}/api/admin/plan-prices")
    assert r.status_code == 200
    data = r.json()
    yield data
    # Restore to defaults 9.99/60/7 (problem statement requests reset at end)
    admin_session.put(f"{BASE_URL}/api/admin/plan-prices", json={
        'monthly_price': 9.99,
        'annual_price': 60.0,
        'trial_days': 7,
    })


class TestAdminPlanPrices:

    def test_put_persists_and_get_returns_same(self, admin_session, original_prices):
        payload = {'monthly_price': 11.5, 'annual_price': 75.0, 'trial_days': 10}
        r = admin_session.put(f"{BASE_URL}/api/admin/plan-prices", json=payload)
        assert r.status_code == 200, f"PUT failed: {r.text}"
        data = r.json()
        # response shape: { monthly: {price, ...}, annual: {price, ...}, trial_days }
        assert 'monthly' in data and 'annual' in data
        assert float(data['monthly']['price']) == 11.5
        assert float(data['annual']['price']) == 75.0
        assert int(data['trial_days']) == 10

        # Verify GET (admin)
        g = admin_session.get(f"{BASE_URL}/api/admin/plan-prices")
        assert g.status_code == 200
        gd = g.json()
        assert float(gd['monthly']['price']) == 11.5
        assert float(gd['annual']['price']) == 75.0
        assert int(gd['trial_days']) == 10

    def test_public_plan_config_reflects_updates(self, admin_session):
        # /api/plan/config is public
        pub = requests.get(f"{BASE_URL}/api/plan/config")
        assert pub.status_code == 200
        pd = pub.json()
        # Find monthly + annual prices in the response (shape mirrors admin endpoint)
        # Accept either {monthly:{price}, annual:{price}} or flat keys
        monthly = pd.get('monthly', {}).get('price') if isinstance(pd.get('monthly'), dict) else pd.get('monthly_price')
        annual = pd.get('annual', {}).get('price') if isinstance(pd.get('annual'), dict) else pd.get('annual_price')
        trial = pd.get('trial_days')
        assert float(monthly) == 11.5, f"public monthly={monthly}"
        assert float(annual) == 75.0, f"public annual={annual}"
        assert int(trial) == 10, f"public trial_days={trial}"

    def test_reset_back_to_defaults(self, admin_session):
        r = admin_session.put(f"{BASE_URL}/api/admin/plan-prices", json={
            'monthly_price': 9.99,
            'annual_price': 60.0,
            'trial_days': 7,
        })
        assert r.status_code == 200
        d = r.json()
        assert float(d['monthly']['price']) == 9.99
        assert float(d['annual']['price']) == 60.0
        assert int(d['trial_days']) == 7
