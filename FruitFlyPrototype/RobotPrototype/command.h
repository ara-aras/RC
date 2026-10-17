/**
 * command.h — Command definitions for Robot Prototype
 *
 * Defines the common command structure used by all communication
 * handlers. No subsystem may directly control motors — all must
 * produce a RobotCommand that flows through the control pipeline.
 */

#ifndef COMMAND_H
#define COMMAND_H

#include <cstdint>

// ============================================================
// Command Actions
// ============================================================
enum CommandAction : uint8_t {
    CMD_STOP            = 0,
    CMD_FORWARD         = 1,
    CMD_BACKWARD        = 2,
    CMD_LEFT            = 3,
    CMD_RIGHT           = 4,
    CMD_ROTATE_LEFT     = 5,
    CMD_ROTATE_RIGHT    = 6,
    CMD_HEARTBEAT       = 7,   // heartbeat-only, no movement change
    CMD_EMERGENCY_STOP  = 8,   // latched emergency stop
    CMD_CLEAR_EMERGENCY = 9    // unlatch emergency stop
};

// ============================================================
// Command Sources (priority order, highest first)
// ============================================================
enum CommandSource : uint8_t {
    SRC_NONE              = 0,
    SRC_PHONE_WIFI        = 1,
    SRC_PHONE_BLUETOOTH   = 2,
    SRC_ESP32_CAM_ESPNOW  = 3,
    SRC_SERIAL_DEBUG      = 4
};

// ============================================================
// Robot Command — produced by all communication handlers
// ============================================================
struct RobotCommand {
    CommandSource source;
    CommandAction action;
    uint8_t       speed;
    uint32_t      sequence;
    uint32_t      timestamp;  // millis() at creation
    // RC (manual) drive: a human is steering, so range/bumper/cliff checks
    // do not block this command. E-stop and the source timeout still apply.
    bool          rc = false;
};

// ============================================================
// ESP-NOW Packet — compact wire format
// ============================================================
struct RobotPacket {
    uint32_t magic;
    uint8_t  version;
    uint8_t  source;
    uint8_t  command;
    uint8_t  speed;
    uint32_t sequence;
    uint32_t timestamp;
} __attribute__((packed));

// ============================================================
// Helper: human-readable action name
// ============================================================
inline const char* actionName(CommandAction a) {
    switch (a) {
        case CMD_STOP:         return "STOP";
        case CMD_FORWARD:      return "FORWARD";
        case CMD_BACKWARD:     return "BACKWARD";
        case CMD_LEFT:         return "LEFT";
        case CMD_RIGHT:        return "RIGHT";
        case CMD_ROTATE_LEFT:  return "ROTATE_LEFT";
        case CMD_ROTATE_RIGHT: return "ROTATE_RIGHT";
        case CMD_HEARTBEAT:       return "HEARTBEAT";
        case CMD_EMERGENCY_STOP:  return "EMERGENCY_STOP";
        case CMD_CLEAR_EMERGENCY: return "CLEAR_EMERGENCY";
        default:                  return "UNKNOWN";
    }
}

// ============================================================
// Helper: human-readable source name
// ============================================================
inline const char* sourceName(CommandSource s) {
    switch (s) {
        case SRC_NONE:              return "NONE";
        case SRC_PHONE_WIFI:        return "PHONE_WIFI";
        case SRC_PHONE_BLUETOOTH:   return "PHONE_BLUETOOTH";
        case SRC_ESP32_CAM_ESPNOW:  return "ESP32_CAM_ESPNOW";
        case SRC_SERIAL_DEBUG:      return "SERIAL_DEBUG";
        default:                    return "UNKNOWN";
    }
}

#endif // COMMAND_H
