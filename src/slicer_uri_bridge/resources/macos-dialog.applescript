on run argv
    if (count of argv) < 3 then error "Expected title, message, and alert kind."
    set alertTitle to item 1 of argv
    set alertMessage to item 2 of argv
    set alertKind to item 3 of argv
    set alertType to informational
    if alertKind is "critical" then
        set alertType to critical
    else if alertKind is "warning" then
        set alertType to warning
    end if
    display alert alertTitle message alertMessage as alertType buttons {"OK"} default button "OK"
end run
