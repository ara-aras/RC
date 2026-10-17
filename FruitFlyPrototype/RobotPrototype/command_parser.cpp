/**
 * command_parser.cpp — Unified command parsing
 *
 * All communication handlers produce a RobotCommand.
 * No parser directly manipulates motor GPIO.
 */

#include "command_parser.h"
#include "config.h"
#include <Arduino.h>

// ============================================================
// Parse single serial character
// ============================================================
bool parseSerialChar(char c, CommandSource source, uint8_t speed, RobotCommand& out) {
    out.source    = source;
    out.speed     = speed;
    out.sequence  = 0;
    out.timestamp = millis();

    switch (c) {
        case 'f': case 'F': out.action = CMD_FORWARD;      return true;
        case 'b': case 'B': out.action = CMD_BACKWARD;     return true;
        case 'l': case 'L': out.action = CMD_LEFT;         return true;
        case 'r': case 'R': out.action = CMD_RIGHT;        return true;
        case 'q': case 'Q': out.action = CMD_ROTATE_LEFT;  return true;
        case 'e': case 'E': out.action = CMD_ROTATE_RIGHT; return true;
        case 's': case 'S': out.action = CMD_STOP;            return true;
        case '!': case 'x': case 'X': out.action = CMD_EMERGENCY_STOP;  return true;
        case 'c': case 'C': out.action = CMD_CLEAR_EMERGENCY; return true;
        default: return false;
    }
}

// ============================================================
// Simple JSON parser (no external library dependency)
// Expects: {"action":"FORWARD","speed":150}
// ============================================================
static CommandAction parseActionString(const String& s) {
    if (s == "FORWARD")         return CMD_FORWARD;
    if (s == "BACKWARD")        return CMD_BACKWARD;
    if (s == "LEFT")            return CMD_LEFT;
    if (s == "RIGHT")           return CMD_RIGHT;
    if (s == "ROTATE_LEFT")     return CMD_ROTATE_LEFT;
    if (s == "ROTATE_RIGHT")    return CMD_ROTATE_RIGHT;
    if (s == "STOP")            return CMD_STOP;
    if (s == "HEARTBEAT")       return CMD_HEARTBEAT;
    if (s == "EMERGENCY_STOP")  return CMD_EMERGENCY_STOP;
    if (s == "CLEAR_EMERGENCY") return CMD_CLEAR_EMERGENCY;
    return CMD_STOP;  // default to stop for unknown
}

static String extractJsonString(const String& json, const String& key) {
    String search = "\"" + key + "\":\"";
    int start = json.indexOf(search);
    if (start < 0) return "";
    start += search.length();
    int end = json.indexOf('"', start);
    if (end < 0) return "";
    return json.substring(start, end);
}

static int extractJsonInt(const String& json, const String& key) {
    String search = "\"" + key + "\":";
    int start = json.indexOf(search);
    if (start < 0) return -1;
    start += search.length();
    // Skip whitespace
    while (start < (int)json.length() && json.charAt(start) == ' ') start++;
    String numStr = "";
    while (start < (int)json.length() && json.charAt(start) >= '0' && json.charAt(start) <= '9') {
        numStr += json.charAt(start);
        start++;
    }
    if (numStr.length() == 0) return -1;
    return numStr.toInt();
}

bool parseJsonCommand(const String& body, RobotCommand& out) {
    String actionStr = extractJsonString(body, "action");
    if (actionStr.length() == 0) return false;

    out.source    = SRC_PHONE_WIFI;
    out.action    = parseActionString(actionStr);
    out.timestamp = millis();
    out.sequence  = 0;

    int spd = extractJsonInt(body, "speed");
    out.speed = (spd >= 0 && spd <= 255) ? (uint8_t)spd : DEFAULT_SPEED;

    // Optional "rc":1 marks a manual (RC car) command; absent means 0.
    out.rc = extractJsonInt(body, "rc") == 1;

    return true;
}
