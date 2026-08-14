# Mounir Project Principles

## Universal configuration

- Mounir must remain universal: different users, installations, and databases must be able to configure the application according to their own environment and preferences.
- Do not hard-code user-specific models, voices, paths, providers, languages, credentials, or configuration choices into application behavior or selection lists.
- Discover available options dynamically from the user's configured service, model, manifest, environment, or database whenever the underlying system supports discovery.
- Persist configuration choices in the user's database and ensure the interface reflects the saved values after reload.
- When a completely general integration is not practical, use the most common open or widely adopted standard and keep provider-specific behavior isolated. For example, prefer OpenAI-compatible interfaces for LLM connections.
- Any unavoidable compatibility limitation must be explicit in the interface and implementation rather than silently assuming one user's setup.
