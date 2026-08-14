on run argv
    if (count of argv) < 3 then error "Expected title, message, and alert kind."
    set alertTitle to item 1 of argv
    set alertMessage to item 2 of argv
    set alertKind to item 3 of argv
    if alertKind is "critical" then
        display alert alertTitle message alertMessage as critical buttons {"OK"} default button "OK"
    else if alertKind is "warning" then
        display alert alertTitle message alertMessage as warning buttons {"OK"} default button "OK"
    else
        display alert alertTitle message alertMessage as informational buttons {"OK"} default button "OK"
    end if
end run
