# Local CI mirror of .github/workflows/ci.yml — dependencies only.
# Build once:  ./build-aux/ci/run-tests.sh --rebuild
# Reused as babble-ci:local until you pass --rebuild again.

FROM docker.io/library/fedora:rawhide

RUN dnf install -y \
    gtk4-devel libadwaita-devel libsoup3-devel libsecret-devel \
    python3-gobject gobject-introspection \
    meson ninja-build gettext desktop-file-utils appstream \
    glib2-devel \
    gtksourceview5-devel libspelling-devel hunspell-en-US \
    xorg-x11-server-Xvfb mesa-libGLES libglvnd-devel \
    dbus-daemon dbus-x11 \
  && dnf clean all

# libsecret needs a session bus; D-Bus refuses to start without a machine id.
RUN dbus-uuidgen > /etc/machine-id

WORKDIR /src
