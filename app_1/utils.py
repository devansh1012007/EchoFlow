import numpy as np
def cosine_distance(vec1, vec2):
    """Calculate cosine distance between two vectors."""
    dot_product = np.dot(vec1, vec2)
    norm_a = np.linalg.norm(vec1)
    norm_b = np.linalg.norm(vec2)
    if norm_a == 0 or norm_b == 0:
        return 1.0  # Max distance if either vector is zero
    return 1 - (dot_product / (norm_a * norm_b))