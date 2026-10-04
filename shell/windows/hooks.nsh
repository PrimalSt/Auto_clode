; Установщик (Tauri NSIS): после установки — agen в PATH пользователя и ядро Jupyter
; «Autogenerator»; перед удалением — убрать их. Делает это само приложение (--register).
; Файл в UTF-8 с BOM: так NSIS точно читает русские строки как UTF-8.
;
; --register не удался (PATH или ядро Jupyter не записаны) — приложение всё равно установлено и
; работает, но установщик предупреждает об этом и завершается с кодом 2: тихая установка (/S)
; сообщения не показывает, а код видит тот, кто её запускал.
!macro NSIS_HOOK_POSTINSTALL
  Push $0
  StrCpy $0 "не запустилась"
  ExecWait '"$INSTDIR\autogenerator.exe" --register' $0
  StrCmp $0 "0" +3
    MessageBox MB_OK|MB_ICONEXCLAMATION "Autogenerator установлен, но команда agen не добавлена в PATH или ядро Jupyter не зарегистрировано (код $0). Приложение работает. Чтобы повторить регистрацию, выполните в терминале: $INSTDIR\autogenerator.exe --register" /SD IDOK
    SetErrorLevel 2
  Pop $0
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  ExecWait '"$INSTDIR\autogenerator.exe" --unregister'
  ; Python приложения пишет байт-код (__pycache__) и для модулей, у которых его не было в
  ; установщике, а деинсталлятор удаляет только свои файлы — папка python осталась бы. В ней
  ; только программа (данные лежат в других папках), поэтому она удаляется целиком.
  IfFileExists "$INSTDIR\python\python.exe" 0 +2
    RMDir /r "$INSTDIR\python"
!macroend
