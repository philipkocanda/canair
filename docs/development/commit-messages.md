# Commit messages

canair uses [Conventional Commits](https://www.conventionalcommits.org/):

```
type(scope): lower-case summary
```

Releases are automated from these subjects. The subject line therefore decides the version bump
and what appears in the changelog:

| | |
|---|---|
| **Types** | `feat`, `fix`, `perf`, `refactor`, `docs`, `test`, `chore`, `ci`, `build`, `revert`, `style`, `deps` |
| **Scope** | the area that the commit touches (optional): `captures`, `monitor`, `pids`, `profiles`, `analysis`, … |
| **Bump** | `feat` → minor · any other type → patch · `!` or a `BREAKING CHANGE:` footer → major |

```bash
git commit -m "fix(captures): count sessions the way --sessions does"
git commit -m "feat(monitor): add a byte ruler"
git commit -m "feat(pids)!: rename the parameters key"   # breaking → major
```

The `commit-msg` hook enforces this format, because the release process silently omits a subject
that does not conform from the release notes instead of rejecting it. Write the subject for the
reader of the changelog. It is the first draft of a release note.
