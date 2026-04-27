property bridgePython : "__BRIDGE_PYTHON__"
property bridgeModule : "__BRIDGE_MODULE__"
property bridgeConfig : "__BRIDGE_CONFIG__"
property launcherLogFile : "__LAUNCHER_LOG__"

on logMessage(messageText)
    set logDir to do shell script "/usr/bin/dirname " & quoted form of launcherLogFile
    do shell script "/bin/mkdir -p " & quoted form of logDir
    do shell script "{ echo '---'; /bin/date; echo " & quoted form of messageText & "; } >> " & quoted form of launcherLogFile & " 2>&1 &"
end logMessage

on open location thisUrl
    set logDir to do shell script "/usr/bin/dirname " & quoted form of launcherLogFile
    do shell script "/bin/mkdir -p " & quoted form of logDir
    do shell script "{ " & ¬
        "echo '---'; " & ¬
        "/bin/date; " & ¬
        "echo 'uri: ' " & quoted form of thisUrl & "; " & ¬
        "echo 'python: ' " & quoted form of bridgePython & "; " & ¬
        "echo 'module: ' " & quoted form of bridgeModule & "; " & ¬
        "echo 'config: ' " & quoted form of bridgeConfig & "; " & ¬
        "PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}; export PATH; " & ¬
        quoted form of bridgePython & " -m " & quoted form of bridgeModule & " " & quoted form of thisUrl & "; " & ¬
        "code=$?; echo 'exit:' $code; " & ¬
        "} >> " & quoted form of launcherLogFile & " 2>&1 &"
end open location

on run
    logMessage("started without URL")
end run
