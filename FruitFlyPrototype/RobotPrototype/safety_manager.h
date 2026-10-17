/**
 * safety_manager.h — Obstacle safety and latched emergency stop enforcement
 */

#ifndef SAFETY_MANAGER_H
#define SAFETY_MANAGER_H

#include "command.h"

void safetyInit();
void safetyUpdate();  // call every loop — reads ultrasonic, updates obstacle state

// Returns true if the command is allowed; false if blocked by safety or E-stop
bool safetyAllowCommand(CommandAction action);

// Emergency stop control (latched)
void safetyTriggerEmergencyStop();
void safetyClearEmergencyStop();
bool safetyIsEmergencyStopped();
bool safetyIsObstacleDetected();

// Why a direction is blocked (for logs / telemetry). Returns nullptr if allowed.
// rc = manual RC drive: only the latched emergency stop can block it.
const char* safetyBlockReason(CommandAction action, bool rc = false);

#endif // SAFETY_MANAGER_H
