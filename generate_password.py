import base64
import hashlib
from cryptography.fernet import Fernet

password = "your-secure-passphrase" 
# Ensure the password is in bytes
password_bytes = password.encode() 

# Hash the password to get a 32-byte key
key = base64.urlsafe_b64encode(hashlib.sha256(password_bytes).digest())

print(key.decode())