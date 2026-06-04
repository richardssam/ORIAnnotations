./makepackage.csh
/Applications/OpenRV.app/Contents/MacOS/rvpkg -force -remove "OTIO Sync Plugin"
/Applications/OpenRV.app/Contents/MacOS/rvpkg -force -add "/Users/sam/Library/Application Support/RV" otiosyncdemo-1.2.rvpkg
/Applications/OpenRV.app/Contents/MacOS/rvpkg -force -install "OTIO Sync Plugin"
/Applications/OpenRV.app/Contents/MacOS/rvpkg -force -optin "OTIO Sync Plugin"
