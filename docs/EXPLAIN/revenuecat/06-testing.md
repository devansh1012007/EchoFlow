# RevenueCat Integration — Testing

## Test Organization

```
backend/app/tests/
├── test_revenuecat.py          # 21 backend tests (all passing)
└── ...

frontend/sample_frontend/src/
├── stores/__tests__/
│   └── subscription.test.tsx    # 3 tests (subscription context)
└── components/subscription/__tests__/
    └── Paywall.test.tsx         # 3 tests (paywall component)
```

## Backend Tests

### Test File: `backend/app/tests/test_revenuecat.py`

**21 tests covering:**

| Test Class | Tests | Coverage |
|---|---|---|
| `TestIsPro` | 5 | `User.is_pro()` method logic |
| `TestSyncEntitlements` | 4 | `sync_entitlements()` service |
| `TestSubscriptionStatusView` | 3 | `GET /subscription/` endpoint |
| `TestSubscriptionManageView` | 2 | `GET /subscription/manage/` endpoint |
| `TestSubscriptionSyncView` | 2 | `POST /subscription/sync/` endpoint |
| `TestRevenueCatWebhookView` | 3 | `POST /webhooks/revenuecat/` endpoint |
| `TestFreeTierUploadLimits` | 2 | Free-tier gating enforcement |

### Running Backend Tests

```bash
# All RevenueCat tests
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm \
  -e PYTHONPATH=/app web pytest backend/app/tests/test_revenuecat.py -v

# Specific test class
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm \
  -e PYTHONPATH=/app web pytest backend/app/tests/test_revenuecat.py::TestIsPro -v

# Single test
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm \
  -e PYTHONPATH=/app web pytest backend/app/tests/test_revenuecat.py::TestIsPro::test_grace_period_extension -v
```

### Key Test Patterns

#### Mocking RevenueCat API

```python
from unittest import mock

def test_sync_sets_pro_when_entitlement_active(self, user, settings):
    from backend.app.services.revenuecat import sync_entitlements
    
    settings.REVENUECAT_SECRET_KEY = "test-secret"
    settings.REVENUECAT_ENTITLEMENT_ID = "pro"
    
    fake_subscriber = {
        "entitlements": {
            "pro": {
                "product_id": "pro",
                "is_active": True,
                "expires_date_ms": str(int((timezone.now() + timedelta(days=30)).timestamp() * 1000)),
            }
        }
    }
    
    with mock.patch("backend.app.services.revenuecat.get_subscriber_info", return_value=fake_subscriber):
        changed = sync_entitlements(user)
    
    user.refresh_from_db()
    assert user.has_pro_entitlement is True
    assert changed is True
```

#### Testing Free-Tier Limits

```python
def test_free_user_blocked_after_daily_limit(self, auth_client, user, settings):
    from backend.app.models import AudioClip
    
    limit = 5
    for i in range(limit):
        AudioClip.objects.create(title=f"clip-{i}", creator=user, status="ready")
    
    r = auth_client.post("/clips/", {}, format='json')
    assert r.status_code == 403
```

#### Testing Grace Period

```python
def test_grace_period_extension(self, user):
    user.has_pro_entitlement = False
    user.pro_expires_at = timezone.now() - timedelta(days=1)
    user.pro_grace_until = timezone.now() + timedelta(days=2)
    assert user.is_pro() is True

def test_grace_period_expired(self, user):
    user.has_pro_entitlement = False
    user.pro_expires_at = timezone.now() - timedelta(days=5)
    user.pro_grace_until = timezone.now() - timedelta(days=1)
    assert user.is_pro() is False
```

## Frontend Tests

### Test Files

| File | Tests | Framework |
|---|---|---|
| `src/stores/__tests__/subscription.test.tsx` | 3 | vitest + @testing-library/react |
| `src/components/subscription/__tests__/Paywall.test.tsx` | 3 | vitest + @testing-library/react |

### Running Frontend Tests

```bash
cd frontend/sample_frontend

# All tests
npx vitest run

# Specific file
npx vitest run src/stores/__tests__/subscription.test.tsx

# Watch mode
npx vitest
```

### Key Test Patterns

#### Mocking API with `vi.hoisted`

```typescript
const mocks = vi.hoisted(() => ({
  getManageUrl: vi.fn(),
  sync: vi.fn(),
  getStatus: vi.fn(),
}));

vi.mock('../../api/client', () => ({
  subscriptionAPI: {
    getStatus: mocks.getStatus,
    sync: mocks.sync,
    getManageUrl: mocks.getManageUrl,
  },
}));

// In tests
mocks.getStatus.mockResolvedValue({
  is_pro: true,
  expires_at: '2026-12-31T23:59:59Z',
  grace_until: null,
  last_synced: '2026-09-06T00:00:00Z',
  limits: { daily_uploads_remaining: 'unlimited' },
});
```

#### Testing React Context with Wrapper

```typescript
const { result } = renderHook(() => useSubscription(), {
  wrapper: ({ children }) => <SubscriptionProvider>{children}</SubscriptionProvider>,
});

await waitFor(() => {
  expect(result.current.isPro).toBe(true);
});
```

#### Testing Paywall Component

```typescript
// Mock auth context
vi.mock('../../stores/auth', () => ({
  useAuth: () => ({
    user: { id: 1, username: 'testuser' },
    authed: true,
    loading: false,
  }),
}));

it('renders upgrade button', () => {
  render(<Paywall />);
  expect(screen.getByText('Upgrade to Pro')).toBeInTheDocument();
});

it('calls handleUpgrade and triggers sync', async () => {
  render(<Paywall />);
  const btn = screen.getByText('Upgrade to Pro');
  btn.click();
  await waitFor(() => {
    expect(mocks.sync).toHaveBeenCalled();
  });
});
```

## Test Environment

### Backend
- **Database**: PostgreSQL (Docker `pgvector/pgvector:pg16`)
- **Redis**: Two instances (broker + cache)
- **MinIO**: S3-compatible object storage
- **Test runner**: pytest + pytest-django
- **Conftest**: Auto-creates `echoflow_test` DB, installs pgvector on template1

### Frontend
- **Runner**: vitest (Vite-native test runner)
- **Environment**: jsdom (via `test: { environment: 'jsdom' }`)
- **Libraries**: @testing-library/react, @testing-library/jest-dom
- **Mocking**: vi.hoisted for module mocks

## CI Integration

```yaml
# .github/workflows/django.yml (excerpt)
- name: Run backend tests
  run: |
    docker compose -f docker-compose.yml -f docker-compose.test.yml up --build -d
    docker compose exec -e PYTHONPATH=/app web pytest backend/app/tests/ --tb=short

- name: Run frontend tests
  run: |
    cd frontend/sample_frontend
    npx vitest run
```

## Coverage

```bash
# Backend coverage
docker compose exec -e PYTHONPATH=/app web pytest backend/app/tests/ \
  --cov=backend.app --cov-report=term-missing

# Frontend coverage
cd frontend/sample_frontend
npx vitest run --coverage
```

## Common Test Issues

| Issue | Solution |
|---|---|
| `vi.mock` factory hoisting | Use `vi.hoisted()` for mock functions needed in test body |
| RevenueCat SDK not available | Mock `@revenuecat/purchases-js` with `vi.mock()` |
| Auth context missing | Provide mock `useAuth` via `vi.mock('../../stores/auth')` |
| Async state updates | Use `waitFor()` from @testing-library/react |
| Window.open not implemented in jsdom | Test `subscriptionAPI.sync` called instead of `window.open` |

## Adding New Tests

When adding RevenueCat features:

1. **Backend**: Add to `test_revenuecat.py` in appropriate test class
2. **Frontend**: Add to existing test files or create new ones in `__tests__/` directories
3. **Mock external calls**: Always mock RevenueCat API and SDK
4. **Test edge cases**: Grace period, missing config, network errors
5. **Follow naming**: `Test<Feature>::test_<scenario>`