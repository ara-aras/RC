/**
 * safety_manager.cpp — Obstacle safety with hysteresis & Latched Emergency Stop
 *
 * Obstacle:
 *   Stop threshold  = 30 cm  (block forward motion)
 *   Clear threshold = 35 cm  (allow forward motion again)
 *
 * Emergency Stop:
 *   Latched state entered on CMD_EMERGENCY_STOP.
 *   Rejects all movement commands until explicitly cleared by CMD_CLEAR_EMERGENCY.
 *   No remote source may bypass emergency stop.
 */

#include "safety_manager.h"
#include "config.h"
#include "ultrasonic.h"
#include "bump.h"
#include "cliff.h"
#include "robot_state.h"
#include "motor_controller.h"
#include <Arduino.h>

// Which directions each hazard blocks. Rotation swings both ends, so the
// bumpers and cliffs block it too; a bump still lets you drive away.
static bool movesForward(CommandAction a)  { return a == CMD_FORWARD  || a == CMD_LEFT || a == CMD_RIGHT; }
static bool movesBackward(CommandAction a) { return a == CMD_BACKWARD; }
static bool rotates(CommandAction a)       { return a == CMD_ROTATE_LEFT || a == CMD_ROTATE_RIGHT; }

static bool _obstacleDetected = false;
static bool _emergencyStop    = false;
static bool _prevObstacle     = false;

void safetyInit() {
    _obstacleDetected = false;
    _emergencyStop    = false;
    _prevObstacle     = false;
    Serial.println(F("[SAFETY] Initialized"));
}

void safetyUpdate() {
    float dist = ultrasonicGetDistanceCm();

    // Hysteresis logic
    if (_obstacleDetected) {
        // Currently blocked — clear only when distance exceeds clear threshold
        if (dist >= OBSTACLE_CLEAR_CM) {
            _obstacleDetected = false;
        }
    } else {
        // Currently clear — block when distance drops to stop threshold
        if (dist <= OBSTACLE_STOP_CM) {
            _obstacleDetected = true;
        }
    }

    // Update central state
    stateSetDistance(dist);
    stateSetObstacle(_obstacleDetected);
    stateSetEmergencyStop(_emergencyStop);
    stateSetBump(bumpFront(), bumpRear());
    stateSetCliff(cliffFront(), cliffRear());

    // Log changes only (not every loop)
    if (_obstacleDetected && !_prevObstacle) {
        Serial.print(F("[SAFETY] Obstacle detected at "));
        Serial.print(dist, 1);
        Serial.println(F(" cm — blocking forward"));
    } else if (!_obstacleDetected && _prevObstacle) {
        Serial.print(F("[SAFETY] Obstacle cleared at "));
        Serial.print(dist, 1);
        Serial.println(F(" cm — forward allowed"));
    }
    _prevObstacle = _obstacleDetected;
}

const char* safetyBlockReason(CommandAction action, bool rc) {
    if (action == CMD_STOP || action == CMD_CLEAR_EMERGENCY || action == CMD_HEARTBEAT) {
        return nullptr;
    }
    // Latched Emergency Stop blocks all movement
    if (_emergencyStop) return "emergency stop";

    // Manual RC drive: the human is the obstacle sensor
    if (rc) return nullptr;

    // Front range sensor blocks only forward motion
    if (_obstacleDetected && action == CMD_FORWARD) return "obstacle ahead";

    // Cliffs are latched; block anything that moves toward the drop
    if (cliffFront() && (movesForward(action) || rotates(action)))  return "cliff ahead";
    if (cliffRear()  && (movesBackward(action) || rotates(action))) return "cliff behind";

    // Bumpers: block driving into the thing; allow driving away
    if (bumpFront() && (movesForward(action) || rotates(action)))   return "front bumper";
    if (bumpRear()  && (movesBackward(action) || rotates(action)))  return "rear bumper";

    return nullptr;
}

bool safetyAllowCommand(CommandAction action) {
    return safetyBlockReason(action) == nullptr;
}

void safetyTriggerEmergencyStop() {
    if (!_emergencyStop) {
        Serial.println(F("[SAFETY] *** LATCHED EMERGENCY STOP ACTIVATED ***"));
    }
    _emergencyStop = true;
    stateSetEmergencyStop(true);
    motorEmergencyStop();
}

void safetyClearEmergencyStop() {
    if (_emergencyStop) {
        Serial.println(F("[SAFETY] Latched emergency stop cleared by user"));
    }
    _emergencyStop = false;
    stateSetEmergencyStop(false);
    cliffClear();   // the same explicit acknowledgement releases cliff latches
}

bool safetyIsEmergencyStopped() {
    return _emergencyStop;
}

bool safetyIsObstacleDetected() {
    return _obstacleDetected;
}
