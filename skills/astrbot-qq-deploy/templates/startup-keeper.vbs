' Silent launcher for AstrBot keep-alive daemon.
' Put this file in:
'   %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\
' Edit the two paths below before use.
'
' Why .vbs instead of a Scheduled Task: registering a task needs
' elevation, and a UAC prompt cannot be raised from a non-interactive
' session. The Startup folder needs no admin rights at all.

Dim sh, cmd
Set sh = CreateObject("WScript.Shell")

cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""D:\AstrBot\bin\keepalive.ps1"" " & _
      "-AstrBotRoot ""D:\AstrBot"" " & _
      "-NapCatShell ""D:\NapCat\NapCat.XXXX.Shell"" " & _
      "-IntervalSec 300"

' 0 = hidden window, False = do not wait
sh.Run cmd, 0, False
