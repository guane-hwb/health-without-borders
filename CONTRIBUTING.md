# Contributing to Health Without Borders

First off, thank you for considering contributing to Health Without Borders! It's people like you that make open source tools for humanitarian aid possible. 

We welcome contributions of all kinds: bug reports, feature requests, documentation improvements, and code patches.

## 1. Where do I go from here?

If you've noticed a bug or have a feature request, please **open an issue** on our GitHub repository. If you want to contribute code, check our issues board for issues labeled `good first issue` or `help wanted`.

## 2. Local Setup

To start contributing, you'll need to set up your local environment. We use FastAPI, PostgreSQL, and `uv` for dependency management. 

Please refer to our [Local Setup Guide](docs/development/setup.md) for step-by-step instructions on spinning up the database via Docker and seeding the clinical catalogs.

## 3. Pull Request & QA Workflow

We take code quality seriously, especially because this backend handles sensitive medical data. 

Before you write any code, please read our **[Quality Assurance and PR Workflow](docs/development/qa-plan.md)**. 
Key takeaways from the QA plan:
* **Branching:** Always branch off and open your Pull Requests against the `develop` branch.
* **Testing:** Ensure all local tests pass (`uv run pytest`) before opening a PR.
* **Schemas:** Respect the Pydantic schemas and our WHO ICD-10/11 mappings.

## 4. Code of Conduct

This project and everyone participating in it is governed by the [Health Without Borders Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code.