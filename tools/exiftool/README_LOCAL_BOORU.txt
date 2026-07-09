Local Booru embedded ExifTool slot

Release/EXE build should ship ExifTool here:
  tools/exiftool/exiftool.exe
  tools/exiftool/exiftool_files/

The app searches this folder first, then the workspace folder:
  Local_Booru_Archive/settings/tools/exiftool/

If ExifTool is missing on Windows, drag-export metadata embedding will attempt
an automatic download of the official 64-bit ExifTool package into the workspace
tools folder. The exported media copy is still created if the download fails,
but XMP/IPTC metadata cannot be embedded until ExifTool is available.
