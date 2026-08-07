# Commit messages

canair uses [Conventional Commits](https://www.conventionalcommits.org/):

```
type(scope): lower-case summary
```

Releases are automated from these subjects, so the subject line decides the
version bump and what lands in the changelog:

| | |
|---|---|
| **Types** | `feat`, `fix`, `perf`, `refactor`, `docs`, `test`, `chore`, `ci`, `build`, `revert`, `style`, `deps` |
| **Scope** | the area touched — `captures`, `monitor`, `pids`, `profiles`, `analysis`, … (optional) |
| **Bump** | `feat` → minor · any other type → patch · `!` or a `BREAKING CHANGE:` footer → major |

```bash
git commit -m "fix(captures): count sessions the way --sessions does"
git commit -m "feat(monitor): add a byte ruler"
git commit -m "feat(pids)!: rename the parameters key"   # breaking → major
```

The `commit-msg` hook enforces this, because a non-conforming subject is
*silently* omitted from the release notes rather than rejected. Write the subject
for the changelog reader — it is the first draft of a release note.
