# Commit-Push-PR Command

Daily workflow for committing changes, pushing to remote, and creating pull requests.

## Process

### 1. Test Suite
- Run full test suite
- If any tests fail, fix failures and re-run before proceeding
- Confirm all tests pass

### 2. Code Quality
- Run linting tools (eslint, pylint, flake8, etc. as applicable)
- Run formatters (black for Python, prettier for JavaScript/YAML)
- Fix all lint warnings and formatting issues
- Stage cleaned-up files

### 3. Stage Changes
- Review git status for all modified/new files
- Stage relevant project files (source, tests, config, docs)
- Explicitly exclude:
  - `.env` files with credentials
  - `credentials.json` or similar secrets
  - `.DS_Store` and other OS files
  - `node_modules/`, `__pycache__/`, other build artifacts
- Verify staging with `git diff --cached`

### 4. Conventional Commit
- Write commit message using conventional commits format:
  - `feat: [description]` - New feature
  - `fix: [description]` - Bug fix
  - `refactor: [description]` - Code refactoring
  - `docs: [description]` - Documentation changes
  - `test: [description]` - Test additions or fixes
  - `chore: [description]` - Build, deps, tooling
- Include body with reasoning if changes are significant
- Optional: Use `$ARGUMENTS` to override message if provided

### 5. Commit & Push
- Create commit with message
- Push to current branch with `-u` flag for first push
- Handle merge conflicts if any

### 6. Create Pull Request
- Create PR with:
  - **Title**: Concise summary (mirror commit subject)
  - **Description**:
    - Summary of changes (1-3 bullet points)
    - Test results from step 1
    - Breaking changes (if any)
    - Reviewer notes or concerns
- Link related issues if applicable
- Request review from appropriate team members

## Input

Optional commit message override from `$ARGUMENTS`

## Output

- Confirmation of successful commit and push
- PR URL for reference
- Summary of what was published
