# PII Encryption (Email)

## Overview

**Fernet symmetric encryption** for user email addresses at rest.

**Implementation:** `backend/app/models.py:16-25`

---

## Implementation

### Key Loading (Startup)
```python
FERNET_KEY = os.environ.get('FIELD_ENCRYPTION_KEY')

if not FERNET_KEY:
    raise ImproperlyConfigured(
        "FIELD_ENCRYPTION_KEY is missing. Application cannot start without PII encryption."
    )

cipher_suite = Fernet(FERNET_KEY.encode())
```

**Fail-fast:** App crashes on startup if key missing — no silent plaintext fallback.

### User Model
```python
class User(AbstractUser):
    encrypted_email = models.TextField(unique=True, null=True, blank=True)
    
    def save(self, *args, **kwargs):
        if self.email and cipher_suite:
            self.encrypted_email = cipher_suite.encrypt(self.email.encode()).decode()
        elif self.email and not cipher_suite:
            logger.warning(f"Saving email in plaintext for user {self.username} due to missing encryption key.")
            self.encrypted_email = self.email
        super().save(*args, **kwargs)
```

### Flow
```
User registers (POST /auth/register/)
       │
       ▼
User.save() called
       │
       ├── email = "user@example.com"
       ├── cipher_suite.encrypt(b"user@example.com") → encrypted bytes
       ├── .decode() → base64 string
       ├── encrypted_email = "gAAAAABl7..." (stored in DB)
       └── email field = "user@example.com" (Django auth uses this)
```

### Authentication
- Django uses `email` field (AbstractUser) for login
- `encrypted_email` **only for storage** — not used for auth
- `encrypted_email` unique → prevents duplicate accounts

---

## Key Management

### Generation
```bash
# Generate new key
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Output: base64 string (44 chars)
```

### Environment Variable
```bash
# .env
FIELD_ENCRYPTION_KEY=gAAAAABl7...  # 44-char base64
```

### Docker/Production
```yaml
# docker-compose.yml
environment:
  - FIELD_ENCRYPTION_KEY=${FIELD_ENCRYPTION_KEY}
```

**Never commit key to repo** — only in `.env` (gitignored) or secret manager.

---

## Encryption Details

### Fernet (AES-128-CBC + HMAC-SHA256)
| Property | Value |
|----------|-------|
| Algorithm | AES-128-CBC |
| Integrity | HMAC-SHA256 |
| Key size | 256-bit (32 bytes) |
| IV | Random per encryption |
| Format | `version || timestamp || IV || ciphertext || HMAC` |
| Output | URL-safe base64 |

### Properties
- **Authenticated encryption** — tampering detected
- **Non-deterministic** — same email → different ciphertext each time
- **Timestamp embedded** — can detect old ciphertexts

---

## Database Storage

```sql
-- app_user table
encrypted_email TEXT UNIQUE  -- Fernet ciphertext (base64)
email VARCHAR(254)           -- Plaintext (for Django auth)
```

**Query by email:**
```python
# Django auth uses email field
User.objects.get(email="user@example.com")  # Works

# Encrypted email not used for queries
User.objects.get(encrypted_email="gAAAAABl7...")  # Not needed
```

---

## Key Rotation (NOT IMPLEMENTED)

### Current Limitation
- **Single key** for all users
- **No rotation** — compromise = all emails exposed
- **No versioning** — can't decrypt old ciphertexts with new key

### Required for Production
```python
# Versioned encryption
class EncryptedEmailField(models.TextField):
    def __init__(self, *args, **kwargs):
        self.key_version = kwargs.pop('key_version', 1)
        super().__init__(*args, **kwargs)
    
    def get_prep_value(self, value):
        key = KEYS[self.key_version]  # Multiple keys
        return f"v{self.key_version}$" + Fernet(key).encrypt(value.encode()).decode()
    
    def from_db_value(self, value, expression, connection):
        if not value: return None
        version = int(value.split('$')[0][1:])
        key = KEYS[version]
        return Fernet(key).decrypt(value.split('$')[1].encode()).decode()
```

### Rotation Process
1. Generate new key → `FIELD_ENCRYPTION_KEY_V2`
2. Add to `KEYS = {1: key1, 2: key2}`
3. New users → encrypt with v2
4. Background job: re-encrypt v1 → v2
5. Remove v1 after migration

---

## Security Analysis

| Aspect | Status | Notes |
|--------|--------|-------|
| Encryption at rest | ✅ | Fernet AES-128-CBC + HMAC |
| Key in env var | ✅ | Not in code/repo |
| Fail-fast on missing key | ✅ | App won't start |
| Unique constraint | ✅ | Prevents duplicate accounts |
| Key rotation | ❌ | **Critical gap** |
| Key versioning | ❌ | Can't rotate without downtime |
| Per-user keys | ❌ | Single key for all |
| HSM/KMS integration | ❌ | Key in memory |

---

## Compliance

| Regulation | Requirement | Status |
|------------|-------------|--------|
| GDPR Art. 32 | Encryption at rest | ✅ |
| GDPR Art. 17 | Right to erasure | ⚠️ Need key destruction |
| CCPA | Encryption | ✅ |
| SOC 2 | Encryption at rest | ✅ |

---

## Testing

```python
# Test encryption
user = User.objects.create_user(username='test', email='test@example.com')
assert user.encrypted_email != 'test@example.com'
assert user.encrypted_email.startswith('gAAAAA')  # Fernet prefix

# Test decryption (not in app code)
from cryptography.fernet import Fernet
cipher = Fernet(settings.FIELD_ENCRYPTION_KEY.encode())
plaintext = cipher.decrypt(user.encrypted_email.encode()).decode()
assert plaintext == 'test@example.com'
```

---

## Discrepancy: Code Comment vs Implementation

| Comment in `models.py:36-42` | Actual Behavior |
|------------------------------|-----------------|
| `elif self.email and not cipher_suite: logger.warning(...)` | **Never executes** — app crashes at startup if key missing |

**Fix:** Remove dead code branch or handle gracefully.

---

*Source: `backend/app/models.py:16-43`, `backend/EchoFlow/settings.py:13-21`*