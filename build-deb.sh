#!/bin/bash
# Build script for Audio Sync Master .deb package
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VERSION="1.0.0"
PACKAGE_NAME="audiosync-master"
BUILD_DIR="$SCRIPT_DIR/debian"
LIB_DIR="$BUILD_DIR/usr/lib/$PACKAGE_NAME"
ICON_DIR="$BUILD_DIR/usr/share/icons/hicolor/scalable/apps"

echo "🔧 Building $PACKAGE_NAME v$VERSION..."

# Clean previous build
rm -f "${PACKAGE_NAME}_${VERSION}_all.deb"

# Create directories
mkdir -p "$LIB_DIR"
mkdir -p "$LIB_DIR/ui"
mkdir -p "$ICON_DIR"
mkdir -p "$BUILD_DIR/usr/bin"
mkdir -p "$BUILD_DIR/usr/share/applications"

# Copy Python source files
echo "📦 Copying source files..."
cp main.py "$LIB_DIR/"
cp config.py "$LIB_DIR/"
cp audio_backend.py "$LIB_DIR/"
cp equalizer.py "$LIB_DIR/"
cp ui/__init__.py "$LIB_DIR/ui/"
cp ui/main_window.py "$LIB_DIR/ui/"
cp ui/delay_panel.py "$LIB_DIR/ui/"
cp ui/equalizer_panel.py "$LIB_DIR/ui/"
cp ui/style.css "$LIB_DIR/ui/"

# Copy icon
echo "🎨 Installing icon..."
cp icons/audiosync-master.svg "$ICON_DIR/"

# Set permissions
echo "🔐 Setting permissions..."
chmod 755 "$BUILD_DIR/DEBIAN/postinst"
chmod 755 "$BUILD_DIR/usr/bin/audiosync-master"
chmod 644 "$BUILD_DIR/usr/share/applications/audiosync-master.desktop"
chmod -R 755 "$LIB_DIR"
find "$LIB_DIR" -type f -name "*.py" -exec chmod 644 {} \;
find "$LIB_DIR" -type f -name "*.css" -exec chmod 644 {} \;

# Build the package
echo "📦 Building .deb package..."
dpkg-deb --build "$BUILD_DIR" "${PACKAGE_NAME}_${VERSION}_all.deb"

echo ""
echo "✅ Package built: ${PACKAGE_NAME}_${VERSION}_all.deb"
echo ""
echo "To install:"
echo "  sudo dpkg -i ${PACKAGE_NAME}_${VERSION}_all.deb"
echo "  sudo apt-get install -f  # Install any missing dependencies"
echo ""
echo "To run: audiosync-master"
