# Maintainer: Will Handley <wh260@cam.ac.uk>
pkgname=python-mddraft
pkgver=$(grep '^version = ' pyproject.toml | head -1 | sed 's/.*= "\(.*\)"/\1/')
pkgrel=1
pkgdesc='Email drafting and gated sending over the mddb card substrate'
arch=('any')
url='https://github.com/handley-lab/mddraft'
license=('MIT')
depends=('python' 'python-mddb>=0.0.26' 'python-html2text')

package() {
  cd "$startdir"
  local purelib
  purelib=$(env -u VIRTUAL_ENV PATH=/usr/bin:/bin \
    python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')
  for f in src/mddraft/*.py; do
    install -Dm644 "$f" "$pkgdir/$purelib/mddraft/$(basename "$f")"
  done
  install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
