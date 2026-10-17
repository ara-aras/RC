/**
 * control_manager.cpp — Priority-based command arbitration & failsafe
 *
 * Rules:
 *   1. Emergency STOP is latched and overrides everything.
 *   2. Normal STOP is ALWAYS accepted regardless of source priority.
 *   3. Movement commands require valid source priority:
 *        PHONE_WIFI > PHONE_BLUETOOTH > ESP32_CAM_ESPNOW
 *   4. Movement commands are blocked if obstacle is detected in forward path.
 *   5. Controller timeout (>1500 ms) automatically fails over or stops motors.
 */

#include "control_manager.h"
#include "config.h"
#include "safety_manager.h"
#include "motor_controller.h"
#include "robot_state.h"
#include <Arduino.h>

// Heartbeat timestamps per source
// Index: 0=WIFI, 1=BLUETOOTH, 2=ESPNOW, 3=SERIAL
static uint32_t _lastHeartbeat[4] = {0, 0, 0, 0};

static CommandSource _activeSource   = SRC_NONE;
static CommandAction _currentAction  = CMD_STOP;
static uint8_t       _currentSpeed   = DEFAULT_SPEED;
static bool          _currentRc      = false;   // running motion came from manual RC drive
static CommandSource _prevSource     = SRC_NONE;

static int sourceIndex(CommandSource src) {
    switch (src) {
        case SRC_PHONE_WIFI:       return 0;
        case SRC_PHONE_BLUETOOTH:  return 1;
        case SRC_ESP32_CAM_ESPNOW: return 2;
        case SRC_SERIAL_DEBUG:     return 3;
        default:                   return -1;
    }
}

void controlInit() {
    _activeSource  = SRC_NONE;
    _currentAction = CMD_STOP;
    _currentSpeed  = DEFAULT_SPEED;
    for (int i = 0; i < 4; i++) _lastHeartbeat[i] = 0;
    Serial.println(F("[CONTROL] Initialized — no active source, motors stopped"));
}

void controlHeartbeat(CommandSource source) {
    int idx = sourceIndex(source);
    if (idx >= 0) {
        _lastHeartbeat[idx] = millis();
    }
}

// Check if a source has sent a heartbeat within the timeout window
static bool sourceAlive(CommandSource src) {
    int idx = sourceIndex(src);
    if (idx < 0) return false;
    if (_lastHeartbeat[idx] == 0) return false;
    return (millis() - _lastHeartbeat[idx]) < COMMAND_TIMEOUT_MS;
}

// Determine the highest-priority available source
static CommandSource bestAvailableSource() {
    if (sourceAlive(SRC_PHONE_WIFI))       return SRC_PHONE_WIFI;
    if (sourceAlive(SRC_PHONE_BLUETOOTH))  return SRC_PHONE_BLUETOOTH;
    if (sourceAlive(SRC_ESP32_CAM_ESPNOW)) return SRC_ESP32_CAM_ESPNOW;
    return SRC_NONE;
}

void controlSubmitCommand(const RobotCommand& cmd) {
    // Record heartbeat for known sources
    controlHeartbeat(cmd.source);

    // Heartbeat-only pseudo-commands don't modify movement
    if (cmd.action == CMD_HEARTBEAT) return;

    // ========================================================
    // 1. EMERGENCY STOP (Latched) — Always accepted immediately
    // ========================================================
    if (cmd.action == CMD_EMERGENCY_STOP) {
        safetyTriggerEmergencyStop();
        _currentAction = CMD_STOP;
        stateSetCommand(CMD_STOP);
        return;
    }

    // ========================================================
    // 2. CLEAR EMERGENCY — Explicit command to release latch
    // ========================================================
    if (cmd.action == CMD_CLEAR_EMERGENCY) {
        safetyClearEmergencyStop();
        return;
    }

    // ========================================================
    // 3. NORMAL STOP — ALWAYS accepted from ANY source
    //    (Fix #3: STOP is never rejected due to source priority)
    // ========================================================
    if (cmd.action == CMD_STOP) {
        motorExecuteCommand(CMD_STOP, 0);
        _currentAction = CMD_STOP;
        stateSetCommand(CMD_STOP);
        return;
    }

    // ========================================================
    // 4. MOVEMENT COMMANDS
    // ========================================================

    // Reject if latched Emergency Stop is active
    if (safetyIsEmergencyStopped()) {
        Serial.println(F("[CONTROL] Rejected movement: EMERGENCY STOP is active!"));
        return;
    }

    // Source Priority Check:
    // Serial debug is always accepted for developer workbench testing.
    // For remote sources, verify against priority hierarchy:
    if (cmd.source != SRC_SERIAL_DEBUG) {
        CommandSource best = bestAvailableSource();

        // If another higher-priority source is active and alive, reject lower source
        if (_activeSource != SRC_NONE && sourceAlive(_activeSource) && cmd.source != _activeSource) {
            if (cmd.source > _activeSource) { // larger enum value = lower priority
                // Lower priority source — ignore movement command
                return;
            }
        } else if (cmd.source != best && best != SRC_NONE) {
            // New command from lower priority source while a better source is alive
            if (cmd.source > best) {
                return;
            }
        }
    }

    // Local safety: obstacle, cliffs, bumpers. Blocked movement becomes STOP.
    if (const char* why = safetyBlockReason(cmd.action, cmd.rc)) {
        motorExecuteCommand(CMD_STOP, 0);
        _currentAction = CMD_STOP;
        stateSetCommand(CMD_STOP);
        Serial.print(F("[CONTROL] "));
        Serial.print(actionName(cmd.action));
        Serial.print(F(" blocked: "));
        Serial.println(why);
        return;
    }

    // Execute command
    _activeSource  = cmd.source;
    _currentAction = cmd.action;
    _currentSpeed  = cmd.speed;
    _currentRc     = cmd.rc;

    motorExecuteCommand(_currentAction, _currentSpeed);

    // Update telemetry state
    stateSetSource(_activeSource);
    stateSetCommand(_currentAction);
    stateSetSpeed(_currentSpeed);
}

void controlUpdate() {
    // Update source liveness in telemetry
    bool wifiAlive = sourceAlive(SRC_PHONE_WIFI);
    bool btAlive   = sourceAlive(SRC_PHONE_BLUETOOTH);
    bool espAlive  = sourceAlive(SRC_ESP32_CAM_ESPNOW);

    stateSetWifiAlive(wifiAlive);
    stateSetBluetoothAlive(btAlive);
    stateSetEspNowAlive(espAlive);

    CommandSource best = bestAvailableSource();

    // If active controller timed out, initiate failover
    if (_activeSource != SRC_NONE && _activeSource != SRC_SERIAL_DEBUG) {
        if (!sourceAlive(_activeSource)) {
            Serial.print(F("[CONTROL] Active source "));
            Serial.print(sourceName(_activeSource));
            Serial.println(F(" timed out"));

            if (best != SRC_NONE) {
                Serial.print(F("[CONTROL] Failover to "));
                Serial.println(sourceName(best));
                _activeSource = best;
            } else {
                Serial.println(F("[CONTROL] All sources timed out — failsafe STOP"));
                _activeSource  = SRC_NONE;
                _currentAction = CMD_STOP;
                motorEmergencyStop();
            }

            stateSetSource(_activeSource);
            stateSetCommand(_currentAction);
        }
    }

    // Failsafe: if no source is alive and motors are spinning, stop them
    if (_activeSource == SRC_NONE && best == SRC_NONE && !motorsStopped()) {
        motorEmergencyStop();
        _currentAction = CMD_STOP;
        stateSetCommand(CMD_STOP);
    }

    // Continuous safety: halt if the running motion became unsafe
    // (obstacle appeared, bumper hit, cliff seen) since it was accepted.
    if (_currentAction != CMD_STOP) {
        if (const char* why = safetyBlockReason(_currentAction, _currentRc)) {
            Serial.print(F("[CONTROL] Safety override, stopping: "));
            Serial.println(why);
            motorExecuteCommand(CMD_STOP, 0);
            _currentAction = CMD_STOP;
            stateSetCommand(CMD_STOP);
        }
    }

    // Log source changes
    if (_activeSource != _prevSource) {
        Serial.print(F("[CONTROL] Active controller: "));
        Serial.println(sourceName(_activeSource));
        _prevSource = _activeSource;
    }
}

CommandSource controlGetActiveSource() {
    return _activeSource;
}
