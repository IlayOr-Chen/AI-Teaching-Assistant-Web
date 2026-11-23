"""
Purpose:
    Repository package exposing data persistence abstractions for Firebase RTDB.
Inputs:
    None.
Outputs:
    Exports `FirebaseRepository` and `InMemoryFirebaseAdapter`.
"""

from src.repository.firebase_repository import (
    FirebaseRepository,
    InMemoryFirebaseAdapter,
)

__all__ = ["FirebaseRepository", "InMemoryFirebaseAdapter"]
