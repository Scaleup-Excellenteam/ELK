ELK - sentence autocomplete

Current milestone
-----------------
The repository currently contains only the shared foundations:

1. AutoCompleteData - the result format required by the assignment.
2. normalize_text - the rules used to compare corpus text with user input.
3. Unit tests that describe the expected normalization behavior.

There is intentionally no search index or completion engine yet. Those will be
added in small, testable milestones after the team understands this foundation.

Run the current tests from the repository root:

    python -m unittest discover -s tests -v
