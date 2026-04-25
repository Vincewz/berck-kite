@echo off
echo Lancement Label Studio...
echo Interface disponible sur http://localhost:8080
echo.
call ls-env\Scripts\activate
set LABEL_STUDIO_LOCAL_FILES_SERVING_ENABLED=true
set LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT=%~dp0dataset\raw
label-studio start --port 8080
