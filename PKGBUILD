# Maintainer: Will Handley <wh260@cam.ac.uk>
pkgname=python-mdraft
pkgver=$(grep '^version = ' pyproject.toml | head -1 | sed 's/.*= "\(.*\)"/\1/')
pkgrel=1
pkgdesc='Email drafting and gated sending over the mddb card substrate'
arch=('any')
url='https://github.com/handley-lab/mdraft'
license=('MIT')
depends=('python' 'python-mddb' 'git')

package() {
  cd "$startdir"
  local purelib
  purelib=$(env -u VIRTUAL_ENV PATH=/usr/bin:/bin \
    python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')
  install -Dm644 src/mdraft/__init__.py "$pkgdir/$purelib/mdraft/__init__.py"
  install -Dm644 src/mdraft/_core.py    "$pkgdir/$purelib/mdraft/_core.py"
  install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
