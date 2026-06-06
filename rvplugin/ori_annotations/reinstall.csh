./makepackage.csh
/Applications/OpenRV.app/Contents/MacOS/rvpkg -force -remove "ORI Annotations"
/Applications/OpenRV.app/Contents/MacOS/rvpkg -force -add "/Users/sam/Library/Application Support/RV" oriannotations.zip
/Applications/OpenRV.app/Contents/MacOS/rvpkg -force -install "ORI Annotations"
