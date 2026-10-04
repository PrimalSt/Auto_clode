; Установщик (Tauri NSIS): после установки — agen в PATH пользователя и ядро Jupyter
; «Autogenerator»; перед удалением — убрать их. Делает это само приложение (--register).
!macro NSIS_HOOK_POSTINSTALL
  ExecWait '"$INSTDIR\autogenerator.exe" --register'
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  ExecWait '"$INSTDIR\autogenerator.exe" --unregister'
!macroend
